"""Bounded gap tracking for sender-stamped events arriving out of order."""

from __future__ import annotations

from typing import Self, TypedDict

_MAX_RANGES = 128


class HookGapReport(TypedDict):
    """Sequence coverage of the retained hook history."""

    highest_seen: int
    missing_ranges: list[tuple[int, int]]
    missing_count: int
    untracked_through: int


class HookGaps:
    """Track missing ranges, not one entry per missing sequence."""

    __slots__ = ("_high", "_missing", "_untracked_through")
    _high: int
    _missing: list[tuple[int, int]]
    _untracked_through: int

    def __new__(cls) -> Self:
        """Start at sequence one, so a missing prefix is visible."""
        self = super().__new__(cls)
        self._high = 0
        self._missing = []
        self._untracked_through = 0
        return self

    def observe(self, sequence: int) -> bool:
        """Heal tracked gaps, ignore duplicates and refuse expired sequences."""
        if sequence <= self._untracked_through:
            msg = "hook sequence falls in expired gap history"
            raise ValueError(msg)
        if sequence > self._high:
            if sequence > self._high + 1:
                self._missing.append((self._high + 1, sequence - 1))
            self._high = sequence
        elif not self._heal(sequence):
            return False

        if len(self._missing) > _MAX_RANGES:
            _, self._untracked_through = self._missing.pop(0)
        return True

    def _heal(self, sequence: int) -> bool:
        for index, (start, end) in enumerate(self._missing):
            if start <= sequence <= end:
                self._missing[index : index + 1] = [
                    (low, high)
                    for low, high in ((start, sequence - 1), (sequence + 1, end))
                    if low <= high
                ]
                return True
        return False

    def report(self) -> HookGapReport:
        """Describe interior gaps only; trailing drops are not observable."""
        return {
            "highest_seen": self._high,
            "missing_ranges": list(self._missing),
            "missing_count": sum(end - start + 1 for start, end in self._missing),
            "untracked_through": self._untracked_through,
        }

    def restore(self, report: HookGapReport) -> None:
        """Continue deduplication and retain known gaps after a process restart."""
        self._high = report["highest_seen"]
        self._missing = [(low, high) for low, high in report["missing_ranges"]]
        self._untracked_through = report["untracked_through"]

    @staticmethod
    def received(report: HookGapReport, sequence: int) -> bool:
        """Recognize a durable receipt, not a reported sequence gap."""
        return report["untracked_through"] < sequence <= report[
            "highest_seen"
        ] and not any(
            start <= sequence <= end for start, end in report["missing_ranges"]
        )
