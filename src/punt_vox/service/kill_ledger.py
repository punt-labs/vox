"""What ``stop`` did to each voxd process it judged, written as it acted.

A stop's report is built from this ledger alone. An entry is written when stop
spares or signals a process and moves to "gone" only when a settle tick sees
the process exit. Reading the ledger runs no probe, so a probe that fails after
a signal can neither erase the account of what was signalled nor turn the
report into a traceback.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from punt_vox.service.backend import ProcessHandle

__all__ = ["KillLedger", "KillOutcome", "LedgerInvariant"]


@final
class LedgerInvariant(Enum):
    """The properties every ledger transition sequence keeps.

    The ledger is a small state machine -- spare, or signalling, then
    ``settle`` with a ``Termination``, then any number of ``confirm`` calls --
    and the report is built from it alone, so these must hold after every
    step. The test suite enumerates every sequence and asserts each by name.
    """

    K1_SIGNALLED_NAMED_UNTIL_GONE = (
        "every signalled pid is named in account(), and once confirmed gone it "
        "is never reported as still alive"
    )
    K2_REFUSED_NEVER_KILLED = (
        "a process that refused the signal is never reported as killed"
    )
    K3_UNCONFIRMED_STAYS_WATCHED = (
        "a signalled process stays in unconfirmed() until it is seen to exit, "
        "and leaves it then"
    )
    K4_SPARED_NEVER_KILLED = (
        "a spared process is never watched as signalled and never reported as killed"
    )
    K5_CONFIRM_MOVES_ONLY_UNCONFIRMED = (
        "confirm() changes only unconfirmed outcomes, and never a spared one"
    )


@final
class KillOutcome(Enum):
    """Where one judged voxd process stands; the value is its report line."""

    SPARED = "voxd pid {pid} spared: voxd.service still owns it"
    # Written before terminate(): its identity re-check can fail before any
    # signal goes out, so this says stop began, not that a signal landed.
    SIGNALLING = "stop began signalling voxd pid {pid}; its exit was never confirmed"
    SURVIVED = "voxd pid {pid} did not exit after SIGTERM and SIGKILL were delivered"
    TERM_ONLY = (
        "voxd pid {pid} got SIGTERM only and had not exited: the stop deadline "
        "left no time for a grace period before SIGKILL"
    )
    REFUSED = "voxd pid {pid} refused the signal: permission denied"
    KILL_REFUSED = (
        "voxd pid {pid} got SIGTERM, but SIGKILL was refused: permission denied"
    )
    # A refused process that exited on its own: gone, but not stop's kill.
    LEFT = "voxd pid {pid} refused the signal, then exited on its own"
    GONE = "voxd pid {pid} killed"

    def line(self, pid: int) -> str:
        """Return this outcome's report line for *pid*."""
        return self.value.format(pid=pid)

    @property
    def unconfirmed(self) -> bool:
        """Return True for a signalled process not yet seen to exit."""
        return self in {
            KillOutcome.SIGNALLING,
            KillOutcome.SURVIVED,
            KillOutcome.TERM_ONLY,
            KillOutcome.REFUSED,
            KillOutcome.KILL_REFUSED,
        }

    @property
    def exited(self) -> bool:
        """Return True once the process is seen gone, killed or on its own."""
        return self in {KillOutcome.GONE, KillOutcome.LEFT}

    def seen_exiting(self) -> KillOutcome:
        """Return this outcome once the process is seen gone.

        A process that refused every signal left on its own; any other
        signalled process counts as stop's kill.
        """
        return KillOutcome.LEFT if self is KillOutcome.REFUSED else KillOutcome.GONE


@final
class KillLedger:
    """The outcome of every voxd process one stop spared or signalled."""

    __slots__ = ("_entries",)

    _entries: dict[ProcessHandle, KillOutcome]

    _SETTLED: ClassVar[Mapping[Termination, KillOutcome]] = MappingProxyType(
        {
            Termination.EXITED: KillOutcome.GONE,
            Termination.SURVIVED: KillOutcome.SURVIVED,
            Termination.TERM_ONLY: KillOutcome.TERM_ONLY,
            Termination.REFUSED: KillOutcome.REFUSED,
            Termination.KILL_REFUSED: KillOutcome.KILL_REFUSED,
        }
    )

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._entries = {}
        return self

    def judged(self, handle: ProcessHandle) -> bool:
        """Return True once stop has spared or signalled *handle*: never again."""
        return handle in self._entries

    def spare(self, handle: ProcessHandle) -> None:
        """Record that *handle* was left alone because the manager owns it."""
        self._entries[handle] = KillOutcome.SPARED

    def signalling(self, handle: ProcessHandle) -> None:
        """Record *handle* as signalled before the signal goes out.

        If ``terminate()`` then fails, the report still names the process.
        """
        self._entries[handle] = KillOutcome.SIGNALLING

    def settle(self, handle: ProcessHandle, termination: Termination) -> None:
        """Record what ``terminate()`` came to for *handle*."""
        self._entries[handle] = self._SETTLED[termination]

    def unconfirmed(self) -> frozenset[ProcessHandle]:
        """Return the signalled handles not yet seen to exit."""
        return frozenset(h for h, o in self._entries.items() if o.unconfirmed)

    def confirm(self, exited: Iterable[ProcessHandle]) -> None:
        """Mark each signalled handle in *exited* as gone; ignore the rest."""
        for handle in exited:
            outcome = self._entries.get(handle, KillOutcome.SPARED)
            if outcome.unconfirmed:
                self._entries[handle] = outcome.seen_exiting()

    def gone(self, handle: ProcessHandle) -> bool:
        """Return True when stop signalled *handle* and saw it exit."""
        return self._entries.get(handle, KillOutcome.SPARED).exited

    def any_gone(self) -> bool:
        """Return True once any signalled process is confirmed gone."""
        return any(outcome.exited for outcome in self._entries.values())

    def account(self) -> list[str]:
        """Return the report lines: the kills, then each other pid's reason."""
        lines: list[str] = []
        killed = self._pids(KillOutcome.GONE)
        if killed:
            lines.append(f"killed unmanaged voxd pid {', '.join(map(str, killed))}")
        others = sorted(
            (h.pid, o) for h, o in self._entries.items() if o is not KillOutcome.GONE
        )
        lines.extend(outcome.line(pid) for pid, outcome in others)
        return lines

    def _pids(self, outcome: KillOutcome) -> list[int]:
        return sorted(h.pid for h, o in self._entries.items() if o is outcome)
