"""The backing context of the one active Program -- what fills and plays it.

The daemon animates a single Program at a time (R2). Everything that Program's
*content* needs -- which on-disk store its fill grows, the authoring subject, the
directory the player reads, and the ordered prompts a fill draws from -- is bundled
in an immutable :class:`ActiveProgram`. The mutable :class:`ActiveContext` is the
one slot that holds the current :class:`ActiveProgram`, written only by the single
control-channel writer (through a ``SwitchProgram`` signal) and read by the fill
reconciliation and the player. Serialising the swap through the sole writer is the
vox-73m5 fix: a ``play <name>`` never leaves the player or the fill pointed at a
stale pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.filler import FillPlan

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.identifiers import ProgramName
    from punt_vox.voxd.programs.manifest import PlaylistSubject
    from punt_vox.voxd.programs.store import PartStore

__all__ = ["ActiveContext", "ActiveProgram"]


@final
@dataclass(frozen=True, slots=True)
class ActiveProgram:
    """The immutable context backing the one active Program (its fill and player).

    ``prompts`` are the pool's ordered, final generation strings (agent base +
    variations already composed, or the single fallback prompt); the fill draws
    index ``i`` from ``prompts[(i - 1) mod len]``. ``directory`` is where the
    player finds the Part audio -- the store's directory, carried so the player
    resolves it without reopening the store.
    """

    name: ProgramName
    store: PartStore
    subject: PlaylistSubject
    directory: Path
    prompts: tuple[str, ...]

    def to_plan(self) -> FillPlan:
        """Return the fill plan the background fill grows this Program from."""
        return FillPlan(store=self.store, subject=self.subject, prompts=self.prompts)


@final
class ActiveContext:
    """The single mutable slot holding the active Program's backing context.

    Written only by the control-channel's sole consumer (via ``SwitchProgram``),
    so the swap is atomic against every read -- the fill reconciliation asks for
    :meth:`plan` and the player for :meth:`directory` between the writer's
    serialised commands, never mid-swap.
    """

    __slots__ = ("_current",)
    _current: ActiveProgram | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._current = None
        return self

    @property
    def current(self) -> ActiveProgram | None:
        """Return the active context, or ``None`` when the daemon is idle."""
        return self._current

    def switch(self, active: ActiveProgram) -> None:
        """Make ``active`` the backing context of the one animated Program."""
        self._current = active

    def plan(self) -> FillPlan:
        """Return the active fill plan, raising when the daemon holds no Program."""
        return self._require().to_plan()

    def directory(self) -> Path:
        """Return the active Program's directory, raising when the daemon is idle."""
        return self._require().directory

    def _require(self) -> ActiveProgram:
        """Return the active context or raise -- callers run only while one is live."""
        if self._current is None:
            msg = "no active Program: the daemon holds nothing to fill or play"
            raise RuntimeError(msg)
        return self._current
