"""Domain types for vibe signal accumulation: Signal and SignalLog."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Self

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Signal:
    """A single classified hook event with timestamp."""

    signal_type: str  # e.g. "tests-pass", "lint-fail", "git-push-ok"
    timestamp: str  # HH:MM format, e.g. "14:32"

    def to_token(self) -> str:
        """Serialize to wire format: 'tests-pass@14:32'."""
        return f"{self.signal_type}@{self.timestamp}"

    @classmethod
    def from_token(cls, token: str) -> Signal:
        """Parse 'tests-pass@14:32' into a Signal."""
        if not token.strip():
            msg = f"Cannot parse Signal from empty token: {token!r}"
            raise ValueError(msg)
        if "@" not in token:
            return cls(signal_type=token.strip(), timestamp="")
        signal_type, _, timestamp = token.partition("@")
        if not signal_type.strip():
            msg = f"Cannot parse Signal from token with empty type: {token!r}"
            raise ValueError(msg)
        return cls(signal_type=signal_type.strip(), timestamp=timestamp.strip())

    @classmethod
    def now(cls, signal_type: str) -> Signal:
        """Create a Signal stamped with the current local time."""
        return cls(signal_type=signal_type, timestamp=datetime.now().strftime("%H:%M"))


class SignalLog:
    """Mutable ordered collection of session signals.

    Stores at most MAX_ENTRIES signals (LRU — oldest dropped first).
    Serializes to/from the comma-separated wire format used in vox.local.md.
    """

    __slots__ = ("_max_entries", "_signals")

    _signals: list[Signal]
    _max_entries: int

    MAX_ENTRIES: ClassVar[int] = 20  # = MAX_VIBE_SIGNALS

    def __new__(cls, max_entries: int = 20) -> Self:
        """Create an empty SignalLog with the given capacity."""
        self = super().__new__(cls)
        self._signals = []
        self._max_entries = max_entries
        return self

    def append(self, signal: Signal) -> None:
        """Add a signal, evicting the oldest if over capacity."""
        self._signals.append(signal)
        if len(self._signals) > self._max_entries:
            self._signals = self._signals[-self._max_entries :]

    def counts(self) -> dict[str, int]:
        """Return count of each signal_type across the log."""
        result: dict[str, int] = {}
        for s in self._signals:
            result[s.signal_type] = result.get(s.signal_type, 0) + 1
        return result

    def last(self, n: int) -> list[Signal]:
        """Return the most recent n signals."""
        return self._signals[-n:]

    def _scan_signals(self) -> tuple[bool, bool, bool, int, int]:
        """Compute session-state flags used by resolve_tags."""
        counts = self.counts()
        last_few = [s.signal_type for s in self.last(3)]
        ended_with_fail = any(s.endswith("-fail") for s in last_few)
        ended_with_pass = any(s.endswith("-pass") for s in last_few)
        shipped = "git-push-ok" in counts or "pr-created" in counts
        had_fails = sum(c for k, c in counts.items() if k.endswith("-fail"))
        had_passes = sum(c for k, c in counts.items() if k.endswith("-pass"))
        return ended_with_fail, ended_with_pass, shipped, had_fails, had_passes

    def resolve_tags(self) -> str:
        """Map accumulated signals to ElevenLabs expressive tags.

        Deterministic mapping — no LLM needed. Examines signal counts and
        trajectory (how the session ended) to choose 1-2 ElevenLabs tags.
        """
        if not self._signals:
            return "[calm]"
        ended_with_fail, ended_with_pass, shipped, had_fails, had_passes = (
            self._scan_signals()
        )
        if shipped:
            return "[satisfied]" if had_fails == 0 else "[relieved] [satisfied]"
        if had_fails > 0 and ended_with_pass:
            return "[relieved]"
        if ended_with_fail and had_fails > had_passes:
            return "[frustrated] [sighs]"
        if had_passes > 3 and had_fails == 0:
            return "[excited]"
        if had_passes > 0:
            return "[calm]"
        return "[calm]"

    def serialize(self) -> str:
        """Serialize to wire format for storage in vox.local.md."""
        return ",".join(s.to_token() for s in self._signals)

    @classmethod
    def deserialize(cls, raw: str, max_entries: int = 20) -> SignalLog:
        """Parse comma-separated token string into a SignalLog."""
        log = cls(max_entries=max_entries)
        if not raw:
            return log
        for token in raw.split(","):
            token = token.strip()
            if token:
                try:
                    log.append(Signal.from_token(token))
                except ValueError:
                    logger.warning(
                        "SignalLog.deserialize: skipping malformed token %r",
                        token,
                    )
        return log

    def __len__(self) -> int:
        """Return the number of signals in the log."""
        return len(self._signals)
