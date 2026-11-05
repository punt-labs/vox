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

__all__ = ["KillLedger", "KillOutcome"]


@final
class KillOutcome(Enum):
    """Where one judged voxd process stands; the value is its report line."""

    SPARED = "voxd pid {pid} spared: voxd.service still owns it"
    # Written before terminate(): its identity re-check can fail before any
    # signal goes out, so this says stop began, not that a signal landed.
    SIGNALLING = "stop began signalling voxd pid {pid}; its exit was never confirmed"
    SURVIVED = (
        "voxd pid {pid} did not exit after SIGTERM/SIGKILL "
        "(permission denied, or it ignored the signals)"
    )
    TERM_ONLY = (
        "voxd pid {pid} got SIGTERM only and had not exited: the stop deadline "
        "left no time for a grace period before SIGKILL"
    )
    REFUSED = "voxd pid {pid} refused the signal: permission denied"
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
        }


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
            if self._entries.get(handle, KillOutcome.SPARED).unconfirmed:
                self._entries[handle] = KillOutcome.GONE

    def gone(self, handle: ProcessHandle) -> bool:
        """Return True when stop signalled *handle* and saw it exit."""
        return self._entries.get(handle) is KillOutcome.GONE

    def any_gone(self) -> bool:
        """Return True once any signalled process is confirmed gone."""
        return KillOutcome.GONE in self._entries.values()

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
