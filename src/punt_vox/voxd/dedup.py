"""Audio deduplication classes for the voxd daemon."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Self

__all__ = [
    "_DEDUP_WINDOW_SECONDS",
    "_ONCE_DEDUP_MAX_ENTRIES",
    "_ONCE_DEDUP_MAX_TTL_SECONDS",
    "ChimeDedup",
    "DedupHit",
    "OnceDedup",
]

logger = logging.getLogger(__name__)

# Audio deduplication window: skip identical audio within this many seconds.
_DEDUP_WINDOW_SECONDS = 5.0

# Hard cap on per-call ``once`` TTL to bound memory usage. Any caller
# passing a larger value is clamped to this cap with a log warning. The
# biff wall use case targets 600 seconds; 3600 is 6x that, more than
# enough headroom without letting a stray ``once=99999999`` wedge
# ``OnceDedup._seen`` with long-lived entries.
_ONCE_DEDUP_MAX_TTL_SECONDS: float = 3600.0

# Hard cap on the number of tracked keys. Entries are evicted in
# insertion order (oldest first) when the cap is reached. Defensive
# against pathological workloads that insert thousands of unique texts
# faster than the time-based pruner can drop them.
_ONCE_DEDUP_MAX_ENTRIES: int = 1024


class ChimeDedup:
    """Always-on in-memory dedup for chime signals.

    Chimes are event markers (tests-pass, lint-fail, git-push-ok, etc)
    and a user does not want to hear the same event chime twice in rapid
    succession. Unlike speech, chime deduplication is always on and
    keyed only on the signal name. The window matches the legacy
    `AudioDedup` default so the user-visible chime behavior is
    unchanged from versions prior to vox-0e9.
    """

    __slots__ = ("_seen", "_window")

    _window: float
    _seen: dict[str, float]

    def __new__(cls, window: float = _DEDUP_WINDOW_SECONDS) -> Self:
        self = super().__new__(cls)
        self._window = window
        self._seen = {}
        return self

    def should_play(self, signal: str) -> bool:
        """Return True if this chime should play (not a recent duplicate)."""
        key = hashlib.md5(f"chime:{signal}".encode(), usedforsecurity=False).hexdigest()
        now = time.monotonic()
        last = self._seen.get(key)
        if last is not None and (now - last) < self._window:
            return False
        self._seen[key] = now
        cutoff = now - self._window * 2
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        return True


@dataclass(frozen=True)
class DedupHit:
    """Returned when an opt-in speech dedup catches a duplicate request.

    The caller made a synthesize/direct_play request with ``once=<ttl>``
    and an identical text was already played within the TTL window. The
    user has already heard the message --- the caller should NOT treat
    this as an error or retry. The fields support observability (logging
    "wall skipped, already played 53s ago") and future UI surfaces.
    """

    original_played_at: float
    """Wall-clock ``time.time()`` of the original play, in seconds since
    the epoch. Safe to serialize, safe to compare against other wall
    clocks. NOT ``time.monotonic()``."""

    ttl_seconds_remaining: float
    """How many more seconds this dedup key remains valid. When the
    TTL expires, the next identical request will play fresh."""


class OnceDedup:
    """Opt-in in-memory dedup for speech with per-call TTL.

    Callers pass ``once=<ttl_seconds>`` on a synthesize or direct_play
    WebSocket message (or the ``vox unmute --once <seconds>`` CLI flag)
    to suppress duplicate plays of identical text within their chosen
    window. Identical text spoken with different voices or providers
    collapses --- the dedup key is ``md5(text)`` only.

    The motivating use case is ``biff wall``: N Claude Code sessions
    in the same repo independently shell out to ``vox unmute`` on the
    same broadcast text, and the user should hear the announcement
    exactly once. See bead vox-0e9.

    Unlike the legacy always-on ``AudioDedup``, this class is only
    invoked when the caller explicitly opts in. Requests without an
    ``once`` parameter play every time, even if identical to a recent
    one. This preserves the property that ``vox unmute "hello"`` twice
    in quick succession on the CLI produces two audible plays.

    Per-caller TTL semantics: each caller's ``ttl_seconds`` applies to
    THEIR query, not to the stored entry. The dedup question each
    caller asks is "was this text played in the last ttl_seconds?" --- a
    caller passing ``once=60`` will see dedup only if the original play
    was within 60 seconds, regardless of whether an earlier caller
    passed ``once=600``. This matches the intuitive "dedupe within N
    seconds" semantic.

    Concurrency: ``check_and_record`` is atomic under voxd's
    single-threaded asyncio event loop. A failed synthesis path MUST
    call ``rollback`` to remove the zombie entry; otherwise the next
    identical call would be incorrectly deduped against a playback
    that never happened.
    """

    __slots__ = ("_seen",)

    _seen: dict[str, tuple[float, float]]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        # key -> (inserted_monotonic, inserted_wall_clock)
        self._seen = {}
        return self

    def check_and_record(self, text: str, ttl_seconds: float) -> DedupHit | None:
        """Check for a recent duplicate; record this call if none found.

        Args:
            text: The speech text. Used as the dedup key via ``md5``.
            ttl_seconds: Dedup window in seconds. Must be positive.
                Values above ``_ONCE_DEDUP_MAX_TTL_SECONDS`` are
                clamped to the cap with a log warning.

        Returns:
            ``None`` if no duplicate exists within the window --- the
            caller should proceed with synthesis + playback and call
            ``rollback(text)`` on failure so the zombie entry is
            removed.
            ``DedupHit(...)`` if a duplicate was found --- the caller
            should skip the play and return the hit to its client so
            the client can render an observable "deduped" response.

        Raises:
            ValueError: if ``ttl_seconds`` is zero or negative.
        """
        if ttl_seconds <= 0:
            msg = f"ttl_seconds must be positive, got {ttl_seconds}"
            raise ValueError(msg)
        if ttl_seconds > _ONCE_DEDUP_MAX_TTL_SECONDS:
            logger.warning(
                "OnceDedup: ttl_seconds=%.1f exceeds cap %.1f, clamping",
                ttl_seconds,
                _ONCE_DEDUP_MAX_TTL_SECONDS,
            )
            ttl_seconds = _ONCE_DEDUP_MAX_TTL_SECONDS

        key = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
        now_mono = time.monotonic()
        now_wall = time.time()

        existing = self._seen.get(key)
        if existing is not None:
            inserted_mono, inserted_wall = existing
            age = now_mono - inserted_mono
            # Per-caller semantics: the dedup fires only if the age is
            # within THIS caller's ttl_seconds, not the stored entry's.
            if age < ttl_seconds:
                return DedupHit(
                    original_played_at=inserted_wall,
                    ttl_seconds_remaining=ttl_seconds - age,
                )

        self._seen[key] = (now_mono, now_wall)

        # Opportunistic time-based prune: drop entries older than the
        # cap so we never accumulate entries beyond the cap horizon.
        cutoff = now_mono - _ONCE_DEDUP_MAX_TTL_SECONDS
        self._seen = {k: (m, w) for k, (m, w) in self._seen.items() if m > cutoff}

        # Hard cap on dict size. If somehow the time prune left us with
        # more than _ONCE_DEDUP_MAX_ENTRIES, evict oldest-first. This is
        # defensive against pathological inserts at a rate faster than
        # the time pruner can keep up.
        if len(self._seen) > _ONCE_DEDUP_MAX_ENTRIES:
            sorted_items = sorted(self._seen.items(), key=lambda kv: kv[1][0])
            keep = sorted_items[-_ONCE_DEDUP_MAX_ENTRIES:]
            self._seen = dict(keep)

        return None

    def rollback(self, text: str) -> None:
        """Remove a recorded entry for *text*, if present.

        Used when synthesis or playback fails after
        ``check_and_record`` returned None: without a rollback, the
        zombie entry would incorrectly dedup subsequent retries
        against a playback that never happened. Idempotent --- safe
        to call when no entry exists.
        """
        key = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
        self._seen.pop(key, None)
