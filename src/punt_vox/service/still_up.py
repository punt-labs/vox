"""What one ``stop`` settle tick found still running, worded for the report.

Stop distinguishes two kinds of survivor. A voxd stop judged unmanaged was a
target, so it is named as still running. The service manager's own main
process is never a target -- stop only waits for it -- so when it outlives the
deadline it is named as the service's process that did not exit, never as an
unmanaged voxd.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["StillUp"]


@final
class StillUp:
    """The pids one settle tick saw alive: unmanaged voxd, and the manager's own."""

    __slots__ = ("_main", "_unmanaged")

    _unmanaged: tuple[int, ...]
    _main: tuple[int, ...]

    def __new__(cls, unmanaged: Iterable[int], main: Iterable[int]) -> Self:
        self = super().__new__(cls)
        self._unmanaged = tuple(sorted(unmanaged))
        self._main = tuple(sorted(main))
        return self

    def __bool__(self) -> bool:
        """Return True while anything stop is waiting on is still up."""
        return bool(self._unmanaged or self._main)

    def line(self) -> str:
        """Return the detail naming every survivor, unmanaged voxd first."""
        parts: list[str] = []
        if self._unmanaged:
            parts.append(f"voxd pid {self._join(self._unmanaged)} is still running")
        if self._main:
            pids = self._join(self._main)
            parts.append(f"voxd.service's own process pid {pids} did not exit")
        return "; ".join(parts)

    @staticmethod
    def _join(pids: tuple[int, ...]) -> str:
        return ", ".join(str(p) for p in pids)
