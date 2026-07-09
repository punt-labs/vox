"""The background fill -- one cancellable single-flight generation task.

``Filler`` ports the machinery of the old ``PoolFiller`` unchanged: exactly one
generation is ever in flight (a single-flight lock), the in-flight generation is
awaited through ``asyncio.shield`` so a cancel (off / retarget) stops *this* await
without cascading into the uncancellable provider call, and an abandoned
generation's file is discarded rather than orphaned in a pool the caller has left.

The Filler is mode-agnostic and never touches the Program: for each Part it
produces (or fails to) it records the durable manifest entry in the
:class:`PartStore` and *posts a message* (:class:`Produced`,
:class:`PermanentFailure`, or :class:`TransientFailure`) to the
:class:`ControlChannel`, whose single consumer applies the mode-appropriate
transition. It stops when the pool is full or permanent failures hit the cap.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self, final

from punt_vox.voxd.programs.fill_recorder import FillRecorder
from punt_vox.voxd.programs.format import MAX_RETRY
from punt_vox.voxd.programs.part_tags import PartTags
from punt_vox.voxd.programs.producer import (
    PartSpec,
    ProducerBadInputError,
    ProducerTransientError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.music_prompts import PromptSet
    from punt_vox.voxd.programs.album_tags import AlbumTags
    from punt_vox.voxd.programs.control_channel import ControlChannel
    from punt_vox.voxd.programs.producer import Producer
    from punt_vox.voxd.programs.sleeper import Sleeper
    from punt_vox.voxd.programs.store import PartStore

__all__ = ["FillPlan", "FillPlanSource", "Filler"]

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = 2.0


class FillPlanSource(Protocol):
    """Yields the fill plan for the currently-active Program (single-method).

    The daemon owns the active Program's manifest (its subject, store, and
    prompts) and exposes it here; the ControlChannel's fill reconciliation asks
    for the current plan whenever the Program wants filling, so a retune's new
    subject/store flows through automatically.
    """

    def current_plan(self) -> FillPlan:
        """Return the fill plan for the currently-active Program."""
        ...


@final
@dataclass(frozen=True, slots=True)
class FillPlan:
    """What one pool's fill targets: its store, album tags, and per-index prompts.

    ``store`` is the album directory the fill grows -- a retune switches to a
    *different* album, so its parts land in a different store, never colliding
    with the pool the loop is leaving. Two plans that differ in any field are
    distinct, so a retune cancels the old fill and starts a fresh one. ``prompts``
    is the pool's :class:`PromptSet`; track ``i`` composes its generation prompt
    and titles its Part from the ``i``-th variation clause.
    """

    store: PartStore
    tags: AlbumTags
    prompts: PromptSet

    def spec_for(self, index: int) -> PartSpec:
        """Return the generation spec (prompt + ID3 tags) for 1-based ``index``.

        The generation prompt is the composed ``base + variation``; the Part's
        title is the raw variation clause (the base prompt for a fallback pool,
        or ``<album> <index>`` if even that is empty), so a music player labels
        each track distinctly rather than by its bare ``NNN`` filename.
        """
        manifest = self.store.manifest()
        album = manifest.tags.name or manifest.tags.slug()
        variations = self.prompts.variations
        variation = variations[(index - 1) % len(variations)] if variations else ""
        part_tags = PartTags(
            title=variation or self.prompts.base or f"{album} {index}",
            album=album,
            genre=self.tags.style,
            index=index,
            total=manifest.format.pool_size,
        )
        return PartSpec(
            prompt=self.prompts.prompt_for(index - 1), index=index, tags=part_tags
        )


@final
class Filler:
    """Own the single background task that fills one pool via a Producer."""

    __slots__ = (
        "_gen_lock",
        "_inflight",
        "_plan",
        "_producer",
        "_recorder",
        "_sleeper",
        "_task",
    )
    _producer: Producer
    _recorder: FillRecorder
    _sleeper: Sleeper
    _plan: FillPlan | None
    _task: asyncio.Task[None] | None
    _gen_lock: asyncio.Lock
    _inflight: asyncio.Task[Path] | None

    def __new__(
        cls,
        producer: Producer,
        channel: ControlChannel,
        sleeper: Sleeper,
    ) -> Self:
        self = super().__new__(cls)
        self._producer = producer
        self._recorder = FillRecorder(channel)
        self._sleeper = sleeper
        self._plan = None
        self._task = None
        self._gen_lock = asyncio.Lock()
        self._inflight = None
        return self

    @property
    def is_running(self) -> bool:
        """Return whether a fill task is currently live."""
        return self._task is not None and not self._task.done()

    def ensure_running(self, plan: FillPlan) -> None:
        """Start or retarget the background fill for ``plan``.

        The same plan with a live task is a no-op; any difference cancels the old
        task and starts a fresh one -- unless the pool is full or has failed out,
        when nothing generates (it rotates the existing pool instead).
        """
        if self._plan == plan and self.is_running:
            return
        self._cancel_task()
        self._plan = plan
        plan.store.prepare()
        if self._generation_complete(plan):
            return
        task = asyncio.create_task(self._fill(plan))
        task.add_done_callback(self._on_fill_done)
        self._task = task

    def cancel(self) -> None:
        """Cancel the fill task and forget the plan -- no orphaned generation."""
        self._cancel_task()
        self._plan = None

    @staticmethod
    def _generation_complete(plan: FillPlan) -> bool:
        """Return whether ``plan`` wants no more generation (full or failed out).

        The single source of truth for "stop generating": the pool is full or
        permanent failures hit the cap. Both the arming check (``ensure_running``)
        and the fill loop (``_fill``) read it, so they can never disagree on when
        to stop -- the runaway bound and the full-pool stop live in one place.
        """
        return Filler._is_full(plan) or Filler._failed_out(plan)

    @staticmethod
    def _is_full(plan: FillPlan) -> bool:
        """Return whether the ready pool has reached the format's target size."""
        store = plan.store
        return len(store.ready_parts()) >= store.manifest().format.pool_size

    @staticmethod
    def _failed_out(plan: FillPlan) -> bool:
        """Return whether permanent failures hit the cap -- the runaway bound.

        A producer that fails permanently every call (bad prompt, missing key)
        records a failed Part but never a ready one, so without this cap ``_fill``
        regenerates forever, hammering the provider and growing the manifest.
        ``MAX_RETRY`` total permanent failures -- durable in the manifest, so a
        restart cannot resume the runaway -- stops it; each stays observable in
        ``failed_parts``. Transient errors record nothing, so they are untouched.
        """
        store = plan.store
        failed = len(store.manifest().parts) - len(store.ready_parts())
        return failed >= MAX_RETRY

    async def _fill(self, plan: FillPlan) -> None:
        """Generate Parts one at a time until full or the failure cap is hit."""
        while not self._is_full(plan) and not self._failed_out(plan):
            await self._produce_one(plan)
        logger.info("background fill stopping: pool full or failure cap reached")

    async def _produce_one(self, plan: FillPlan) -> None:
        """Produce one Part; record it and post its outcome.

        A transient error backs off and the loop retries; any other error records
        the Part failed and posts a permanent outcome the status surface reports.
        """
        store = plan.store
        index = store.next_index()
        target = store.write_target(index)
        inflight = asyncio.ensure_future(
            self._protected_produce(plan.spec_for(index), target)
        )
        self._inflight = inflight
        try:
            written = await asyncio.shield(inflight)
        except asyncio.CancelledError:
            raise
        except ProducerBadInputError as exc:
            self._recorder.permanent(store, index, target, exc)
            return
        except ProducerTransientError as exc:
            self._recorder.transient(exc)
            await self._sleeper.sleep(_BACKOFF_SECONDS)
            return
        except Exception as exc:
            logger.exception("fill: unexpected error producing part %d", index)
            self._recorder.unexpected(store, index, target, exc)
            return
        finally:
            self._clear_inflight(inflight)
        self._recorder.ready(store, index, written)

    def _clear_inflight(self, inflight: asyncio.Task[Path]) -> None:
        """Drop the in-flight handle once its generation settles."""
        if self._inflight is inflight:
            self._inflight = None

    async def _protected_produce(self, spec: PartSpec, target: Path) -> Path:
        """Run one provider generation under the single-flight lock.

        The lock is held across the whole generation -- including the uncancellable
        provider work -- so concurrency stays bounded to one across an off/retarget.
        """
        async with self._gen_lock:
            await self._producer.produce(spec, target)
            return target

    @staticmethod
    def _on_fill_done(task: asyncio.Task[None]) -> None:
        """Surface any error that escaped the fill task instead of losing it.

        The task should only finish cleanly or by cancellation; a retrieved
        exception here (a store read, a bug) is logged at ERROR, never lost.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("fill task died with an unexpected error", exc_info=exc)

    def _cancel_task(self) -> None:
        """Cancel the live fill task and abandon any in-flight generation."""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        self._abandon_inflight()

    def _abandon_inflight(self) -> None:
        """Detach the in-flight generation so its output is discarded on settle.

        The provider call cannot be cancelled, so rather than orphan the Part it
        writes, we detach it and unlink whatever file it wrote once it settles.
        """
        inflight = self._inflight
        self._inflight = None
        if inflight is None:
            return
        if inflight.done():
            self._discard_generated(inflight)
        else:
            inflight.add_done_callback(self._discard_generated)

    @staticmethod
    def _discard_generated(task: asyncio.Task[Path]) -> None:
        """Unlink the Part an abandoned generation wrote (task done callback)."""
        if task.cancelled() or task.exception() is not None:
            return
        with contextlib.suppress(FileNotFoundError):
            task.result().unlink()
        logger.info("discarded orphaned Part from a cancelled fill")
