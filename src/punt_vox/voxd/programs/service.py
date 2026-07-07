"""The ``ProgramService`` composition root -- the daemon's one Program seam.

``ProgramService`` owns the whole live orchestration for the single active
Program: the :class:`Program` entity, the single-writer :class:`ControlChannel`,
the background :class:`Filler`, the :class:`ProgramLoop` and its player, and the
:class:`ActiveContext` that names which pool is animated. The daemon constructs
one service and registers thin wire handlers that call its handler-facing methods
(:meth:`turn_on`, :meth:`play`, :meth:`advance`, :meth:`off`) and read its
authoritative :meth:`status` per call. Every mutation is posted to the channel and
applied by the sole consumer, so nothing a handler does races the Program (O2).

The service is both the :class:`FillPlanSource` (the fill asks it for the active
plan) and the ``PlayerDirectory`` (the player asks it for the active directory),
delegating both to the one :class:`ActiveContext` the single writer swaps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.music_prompts import PromptSet
from punt_vox.voxd.programs.active_context import ActiveContext, ActiveProgram
from punt_vox.voxd.programs.control_channel import ControlChannel
from punt_vox.voxd.programs.fill_reconciler import FillReconciler
from punt_vox.voxd.programs.filler import Filler
from punt_vox.voxd.programs.format import Format
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.lifecycle_signal import TurnOff
from punt_vox.voxd.programs.loop import ProgramLoop
from punt_vox.voxd.programs.manifest import PlaylistSubject, ProgramManifest
from punt_vox.voxd.programs.playback_health import PlaybackHealth
from punt_vox.voxd.programs.playback_signal import Rotate
from punt_vox.voxd.programs.program import Program
from punt_vox.voxd.programs.rotate_policy import RotatePolicy
from punt_vox.voxd.programs.state import ProgramState
from punt_vox.voxd.programs.status import ProgramStatus
from punt_vox.voxd.programs.subprocess_player import SubprocessPlayer
from punt_vox.voxd.programs.switch_signal import SwitchProgram

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.filler import FillPlan
    from punt_vox.voxd.programs.identifiers import PartRef
    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.producer import Producer
    from punt_vox.voxd.programs.sleeper import Sleeper
    from punt_vox.voxd.programs.store import PartStore, ProgramStore

__all__ = ["ProgramService"]

_DEFAULT_STYLE = "ambient"


@final
class ProgramService:
    """Own and drive the one active Program; the handler-facing daemon seam."""

    __slots__ = (
        "_channel",
        "_context",
        "_filler",
        "_health",
        "_loop",
        "_root",
        "_store",
    )
    _store: ProgramStore
    _root: Path
    _context: ActiveContext
    _channel: ControlChannel
    _filler: Filler
    _health: PlaybackHealth
    _loop: ProgramLoop

    def __new__(
        cls, producer: Producer, store: ProgramStore, root: Path, sleeper: Sleeper
    ) -> Self:
        self = super().__new__(cls)
        self._store = store
        self._root = root
        self._context = ActiveContext()
        self._channel = ControlChannel(Program(ProgramState.initial(), RotatePolicy()))
        self._filler = Filler(producer, self._channel, sleeper)
        self._channel.attach_reconciler(FillReconciler(self._filler, self))
        self._health = PlaybackHealth()
        self._loop = ProgramLoop(
            self._channel, SubprocessPlayer(self), sleeper, self._health
        )
        return self

    # -- injected seams (FillPlanSource + PlayerDirectory) ------------------

    def current_plan(self) -> FillPlan:
        """Return the active fill plan -- the fill reconciliation's source."""
        return self._context.plan()

    def active_directory(self) -> Path:
        """Return the active Program's directory -- the player's source."""
        return self._context.directory()

    # -- daemon lifecycle --------------------------------------------------

    async def serve_control(self) -> None:
        """Run the sole control-channel writer for the daemon's lifetime."""
        await self._channel.serve()

    async def run_playback(self) -> None:
        """Run the playback loop for the daemon's lifetime."""
        await self._loop.run()

    async def run_once(self) -> None:
        """Apply exactly one queued command -- the test seam for the writer."""
        await self._channel.apply_next()

    def shutdown(self) -> None:
        """Cancel any in-flight fill on daemon stop (no orphaned generation)."""
        self._filler.cancel()

    # -- observation (authoritative, read per call, never cached) ----------

    def status(self) -> ProgramStatus:
        """Return the daemon's authoritative Program status (vox-73m5, per call)."""
        active = self._context.current
        if active is None:
            return ProgramStatus.idle()
        return ProgramStatus.of(self._channel.program, active.name, self._health.fault)

    def saved_programs(self) -> tuple[ProgramManifest, ...]:
        """Return every saved Program's manifest, by name (the ``list`` view)."""
        return self._store.list_programs()

    # -- handler-facing mutators (each POSTs one serialized command) --------

    def turn_on(
        self, *, style: str | None, name: str | None, prompts: PromptSet | None
    ) -> None:
        """Turn a Program on: create or resume its pool, then generate/play.

        A brand-new name creates an empty Program that generates its first Part;
        an existing name resumes its saved pool (playing at once, filling only
        below full). Never regenerates a full pool.
        """
        program_name = self._name_for(name, style)
        subject, disk_pool, store = self._prepare_on(program_name, style)
        active = ActiveProgram(
            name=program_name,
            store=store,
            subject=subject,
            directory=self._root / program_name.value,
            prompts=self._final_prompts(prompts, subject.style),
        )
        self._switch(Format.PLAYLIST, active, disk_pool, target=None)

    def play(self, name: ProgramName, part: PartRef | None) -> None:
        """Cold-start replay of a saved Program from disk -- no generation (finding #2).

        Raises ``ValueError`` when the Program is unknown, has no ready Part, or
        the addressed part index is out of range (resolved before any transition,
        finding #7).
        """
        manifest = self._store.resolve(name)
        if manifest is None:
            msg = f"no saved program named {name.value!r}"
            raise ValueError(msg)
        ready = manifest.ready_parts()
        if not ready:
            msg = f"{name.value!r} has no ready parts to play"
            raise ValueError(msg)
        target = self._target_in(ready, part)
        active = ActiveProgram(
            name=name,
            store=self._store.open(name),
            subject=manifest.subject,
            directory=self._root / name.value,
            prompts=self._final_prompts(None, manifest.subject.style),
        )
        self._switch(manifest.format, active, frozenset(ready), target=target)

    def loop(self, name: ProgramName) -> None:
        """Replay a saved Program and rotate on every track end (play, no part)."""
        self.play(name, None)

    def advance(self) -> None:
        """Advance to another Part -- the one ungated skip/next/loop transition."""
        self._channel.post(Rotate())

    def off(self) -> None:
        """Turn the active Program off (stop playback, cancel the fill)."""
        self._channel.post(TurnOff())

    # -- internals ---------------------------------------------------------

    def _switch(
        self,
        fmt: Format,
        active: ActiveProgram,
        disk_pool: frozenset[Part],
        target: Part | None,
    ) -> None:
        """Post a switch to a freshly seeded Program over ``disk_pool``."""
        program = Program(ProgramState.restored(fmt, disk_pool), RotatePolicy())
        self._channel.post(
            SwitchProgram(self._channel, self._context, program, active, target)
        )

    def _prepare_on(
        self, name: ProgramName, style: str | None
    ) -> tuple[PlaylistSubject, frozenset[Part], PartStore]:
        """Resolve the subject, saved pool, and store for a ``turn_on``."""
        existing = self._store.resolve(name)
        if existing is not None:
            return (
                existing.subject,
                frozenset(existing.ready_parts()),
                self._store.open(name),
            )
        subject = self._subject_for(style)
        store = self._store.create(
            ProgramManifest(name=name, fmt=Format.PLAYLIST, subject=subject, parts=())
        )
        return subject, frozenset(), store

    @staticmethod
    def _name_for(name: str | None, style: str | None) -> ProgramName:
        """Derive the Program's addressable name from the request (name, else style)."""
        raw = (name or "").strip() or (style or "").strip() or "music"
        return ProgramName(raw)

    @staticmethod
    def _subject_for(style: str | None) -> PlaylistSubject:
        """Build the authoring subject for a new Program (style is the only axis)."""
        clean = (style or "").strip() or _DEFAULT_STYLE
        return PlaylistSubject(vibe=clean, style=clean)

    @staticmethod
    def _final_prompts(prompts: PromptSet | None, style: str) -> tuple[str, ...]:
        """Compose the pool's ordered final prompt strings the fill draws from.

        An agent set expands to one composed prompt per pool slot; a fallback set
        (no agent prompts) is the single literal prompt every Part reuses.
        """
        chosen = prompts if prompts is not None else PromptSet.fallback(style, "")
        if not chosen.variations:
            return (chosen.base,)
        return tuple(chosen.prompt_for(i) for i in range(Format.PLAYLIST.pool_size))

    @staticmethod
    def _target_in(ready: tuple[Part, ...], part: PartRef | None) -> Part:
        """Resolve the cold-start target Part, raising on an out-of-range index."""
        if part is None:
            return ready[0]
        if not 1 <= part.index <= len(ready):
            msg = f"playlist has {len(ready)} parts; {part.index} is out of range"
            raise ValueError(msg)
        return ready[part.index - 1]
