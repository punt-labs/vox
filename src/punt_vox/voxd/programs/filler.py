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

from punt_vox.voxd.programs.fill_signal import (
    PermanentFailure,
    Produced,
    TransientFailure,
)
from punt_vox.voxd.programs.format import MAX_RETRY
from punt_vox.voxd.programs.identifiers import Reason
from punt_vox.voxd.programs.manifest import PartEntry, PlaylistSubject
from punt_vox.voxd.programs.part import Part, PartStatus
from punt_vox.voxd.programs.producer import (
    PartSpec,
    ProducerBadInputError,
    ProducerTransientError,
)

if TYPE_CHECKING:
    from pathlib import Path

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
    """What one pool's fill targets: its store, subject, and per-index prompts.

    ``store`` is the Program directory the fill grows -- a retune switches to a
    *different* (vibe, style) Program, so its parts land in a different store,
    never colliding with the pool the loop is leaving. Two plans that differ in
    any field are distinct, so a retune cancels the old fill and starts a fresh
    one. ``prompts`` is the pool's ordered prompt variations; index ``i`` draws
    ``prompts[(i - 1) mod len]``.
    """

    store: PartStore
    subject: PlaylistSubject
    prompts: tuple[str, ...]

    def spec_for(self, index: int) -> PartSpec:
        """Return the generation spec for the Part at 1-based ``index``."""
        prompt = self.prompts[(index - 1) % len(self.prompts)] if self.prompts else ""
        return PartSpec(prompt=prompt, index=index)


@final
class Filler:
    """Own the single background task that fills one pool via a Producer."""

    __slots__ = (
        "_channel",
        "_gen_lock",
        "_inflight",
        "_plan",
        "_producer",
        "_sleeper",
        "_task",
    )
    _producer: Producer
    _channel: ControlChannel
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
        self._channel = channel
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
        if self._is_full(plan) or self._failed_out(plan):
            return
        task = asyncio.create_task(self._fill(plan))
        task.add_done_callback(self._on_fill_done)
        self._task = task

    def cancel(self) -> None:
        """Cancel the fill task and forget the plan -- no orphaned generation."""
        self._cancel_task()
        self._plan = None

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
        the Part failed and posts a permanent outcome (OBSERVABLE, vox-ig52).
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
            self._record_and_post(store, self._permanent(index, target, exc))
            return
        except ProducerTransientError as exc:
            self._channel.post(TransientFailure(self._reason(exc, "transient")))
            await self._sleeper.sleep(_BACKOFF_SECONDS)
            return
        except Exception as exc:
            logger.exception("fill: unexpected error producing part %d", index)
            self._record_and_post(store, self._unexpected(index, target, exc))
            return
        finally:
            self._clear_inflight(inflight)
        self._record_and_post(store, self._ready(index, written))

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

    def _record_and_post(
        self, store: PartStore, outcome: tuple[PartEntry, Produced | PermanentFailure]
    ) -> None:
        entry, signal = outcome
        store.record(entry)
        self._channel.post(signal)

    @staticmethod
    def _ready(index: int, target: Path) -> tuple[PartEntry, Produced]:
        part = Part(target.name, index)
        entry = PartEntry(index=index, file=target.name, status=PartStatus.READY)
        return entry, Produced(part)

    @staticmethod
    def _permanent(
        index: int, target: Path, exc: ProducerBadInputError
    ) -> tuple[PartEntry, PermanentFailure]:
        return Filler._failed(index, target, Filler._reason(exc, "permanent"))

    @staticmethod
    def _unexpected(
        index: int, target: Path, exc: Exception
    ) -> tuple[PartEntry, PermanentFailure]:
        """Build a permanent outcome tagged as an unexpected (buggy) failure."""
        return Filler._failed(index, target, Reason(f"unexpected: {exc}"))

    @staticmethod
    def _failed(
        index: int, target: Path, reason: Reason
    ) -> tuple[PartEntry, PermanentFailure]:
        part = Part(target.name, index)
        entry = PartEntry(
            index=index, file=target.name, status=PartStatus.FAILED, reason=reason.text
        )
        return entry, PermanentFailure(part, reason)

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

    @staticmethod
    def _reason(exc: Exception, fallback: str) -> Reason:
        """Build a non-empty Reason from an exception message."""
        return Reason(str(exc) or fallback)
