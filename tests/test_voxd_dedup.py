"""Tests for punt_vox.voxd.dedup -- ChimeDedup, OnceDedup, DedupHit."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import logging

import pytest

from punt_vox.voxd.dedup import ChimeDedup, DedupHit, OnceDedup


class TestOnceDedup:
    """Unit tests for the OnceDedup class.

    Closes vox-0e9. The class deduplicates speech requests when the
    caller passes a TTL window. Identical text spoken with different
    voices, providers, or models all collapse --- the dedup key is
    md5(text) only. Returns DedupHit on a hit so callers can render
    observable "deduped" responses.
    """

    def test_first_call_records_and_returns_none(self) -> None:
        dedup = OnceDedup()
        result = dedup.check_and_record("hello world", ttl_seconds=600)
        assert result is None

    def test_second_call_within_ttl_returns_hit(self) -> None:
        dedup = OnceDedup()
        first = dedup.check_and_record("hello world", ttl_seconds=600)
        assert first is None
        second = dedup.check_and_record("hello world", ttl_seconds=600)
        assert second is not None
        assert isinstance(second, DedupHit)
        assert second.original_played_at > 0
        assert 0 < second.ttl_seconds_remaining <= 600

    def test_second_call_after_ttl_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When monotonic clock advances past the TTL, the entry expires."""
        dedup = OnceDedup()

        clock = [1000.0]

        def fake_monotonic() -> float:
            return clock[0]

        def fake_time() -> float:
            return 1_700_000_000.0 + (clock[0] - 1000.0)

        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", fake_monotonic)
        monkeypatch.setattr("punt_vox.voxd.dedup.time.time", fake_time)

        first = dedup.check_and_record("hello world", ttl_seconds=10)
        assert first is None

        # Advance the clock past the TTL.
        clock[0] = 1011.0

        second = dedup.check_and_record("hello world", ttl_seconds=10)
        assert second is None

    def test_different_text_does_not_dedupe(self) -> None:
        dedup = OnceDedup()
        first = dedup.check_and_record("hello", ttl_seconds=600)
        second = dedup.check_and_record("goodbye", ttl_seconds=600)
        assert first is None
        assert second is None

    def test_key_is_text_only_voice_irrelevant(self) -> None:
        """Two callers with the same text collapse regardless of voice.

        OnceDedup keys on md5(text) only. The voice/provider/model are
        not part of the key per the vox-0e9 spec --- biff wall fan-out
        across N sessions may use different voice settings but the
        user heard the SAME message and shouldn't hear it again.
        """
        dedup = OnceDedup()
        # OnceDedup.check_and_record only takes text + ttl_seconds. The
        # voice is not even an argument --- confirming the key shape by
        # the type signature itself. The test below documents the
        # invariant for future maintainers.
        first = dedup.check_and_record("status update", ttl_seconds=600)
        second = dedup.check_and_record("status update", ttl_seconds=600)
        assert first is None
        assert second is not None

    def test_dedup_hit_carries_original_played_at_wall_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """original_played_at is wall clock (time.time), not monotonic."""
        dedup = OnceDedup()

        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", lambda: 5000.0)
        monkeypatch.setattr("punt_vox.voxd.dedup.time.time", lambda: 1_700_000_000.0)

        first = dedup.check_and_record("text", ttl_seconds=100)
        assert first is None

        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", lambda: 5050.0)
        monkeypatch.setattr("punt_vox.voxd.dedup.time.time", lambda: 1_700_000_050.0)

        hit = dedup.check_and_record("text", ttl_seconds=100)
        assert hit is not None
        # original_played_at is the wall-clock time of the FIRST call,
        # not the second. Caller-facing for "played 50s ago" rendering.
        assert hit.original_played_at == 1_700_000_000.0
        # ttl_seconds_remaining = original ttl - elapsed monotonic.
        assert abs(hit.ttl_seconds_remaining - 50.0) < 0.001

    def test_zero_ttl_raises(self) -> None:
        dedup = OnceDedup()
        with pytest.raises(ValueError, match="positive"):
            dedup.check_and_record("text", ttl_seconds=0)

    def test_negative_ttl_raises(self) -> None:
        dedup = OnceDedup()
        with pytest.raises(ValueError, match="positive"):
            dedup.check_and_record("text", ttl_seconds=-1)

    def test_pruning_drops_entries_older_than_max_ttl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opportunistic prune-on-insert drops entries older than the global cap.

        The cap (``_ONCE_DEDUP_MAX_TTL_SECONDS``) bounds how long any
        single entry can live in ``_seen``, regardless of what TTL the
        original caller requested. This prevents pathological
        ``once=99999999`` callers from wedging long-lived entries.
        """
        from punt_vox.voxd.dedup import _ONCE_DEDUP_MAX_TTL_SECONDS

        dedup = OnceDedup()

        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.dedup.time.time", lambda: 1_700_000_000.0)

        dedup.check_and_record("text-a", ttl_seconds=600)
        assert len(dedup._seen) == 1

        # Advance past the global cap so the entry is prunable.
        clock[0] = 1000.0 + _ONCE_DEDUP_MAX_TTL_SECONDS + 100.0

        # Insert a different text --- this triggers the prune loop.
        dedup.check_and_record("text-b", ttl_seconds=600)
        assert len(dedup._seen) == 1

    def test_rollback_removes_entry(self) -> None:
        """rollback(text) drops the entry so a subsequent call plays again."""
        dedup = OnceDedup()
        first = dedup.check_and_record("wall msg", ttl_seconds=600)
        assert first is None

        # Simulate a failed synthesis --- the dedup entry was recorded
        # but the audio never actually played. Rollback must remove it.
        dedup.rollback("wall msg")

        # A retry should NOT be deduped.
        retry = dedup.check_and_record("wall msg", ttl_seconds=600)
        assert retry is None

    def test_rollback_is_idempotent(self) -> None:
        """rollback on an unrecorded text is a no-op, not an error."""
        dedup = OnceDedup()
        # Never called check_and_record for this text.
        dedup.rollback("unknown text")  # Must not raise.

    def test_per_caller_ttl_shrinks_effective_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each caller's own ttl_seconds decides if an entry is fresh enough.

        Copilot reviewer 3053861452: the first caller's TTL should NOT
        silently extend the dedup window for a later caller that asks
        for a shorter one. Each caller answers its own question of
        "was this played in the last N seconds?"
        """
        dedup = OnceDedup()
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.dedup.time.time", lambda: 1_700_000_000.0)

        # First caller records with a long window.
        first = dedup.check_and_record("text", ttl_seconds=600)
        assert first is None

        # 50 seconds later, a second caller asks with a 30s window.
        clock[0] = 1050.0
        second = dedup.check_and_record("text", ttl_seconds=30)
        # age=50 > caller's ttl of 30 -> NOT a hit from the second
        # caller's perspective. Must not dedupe.
        assert second is None

        # Immediately after, a third caller asks with a 120s window.
        third = dedup.check_and_record("text", ttl_seconds=120)
        # age is now 0 (the second caller's record_and_record reset
        # the entry) so third caller asks "was this played in the
        # last 120s?" --- yes, just now. DedupHit.
        assert third is not None

    def test_ttl_above_cap_gets_clamped(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Callers passing a TTL above the cap are clamped with a log warning."""
        from punt_vox.voxd.dedup import _ONCE_DEDUP_MAX_TTL_SECONDS

        dedup = OnceDedup()
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.dedup.time.time", lambda: 1_700_000_000.0)

        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.dedup"):
            first = dedup.check_and_record("text", ttl_seconds=99_999_999)
        assert first is None
        assert "clamping" in caplog.text

        # Advance past the cap; entry should be prunable.
        clock[0] = 1000.0 + _ONCE_DEDUP_MAX_TTL_SECONDS + 1.0
        second = dedup.check_and_record("text", ttl_seconds=10)
        assert second is None  # entry was pruned, no hit

    def test_hard_cap_on_dict_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When more than _ONCE_DEDUP_MAX_ENTRIES inserted, oldest evicted."""
        from punt_vox.voxd.dedup import _ONCE_DEDUP_MAX_ENTRIES

        dedup = OnceDedup()
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.dedup.time.time", lambda: 1_700_000_000.0)

        # Fill the cache past the cap. Each insert advances the clock
        # slightly so the eviction order is deterministic.
        for i in range(_ONCE_DEDUP_MAX_ENTRIES + 50):
            clock[0] = 1000.0 + (i * 0.001)
            dedup.check_and_record(f"text-{i}", ttl_seconds=600)

        import hashlib

        def _md5(s: str) -> str:
            return hashlib.md5(s.encode(), usedforsecurity=False).hexdigest()

        # Dict size is bounded by the hard cap.
        assert len(dedup._seen) == _ONCE_DEDUP_MAX_ENTRIES
        # The oldest insertions were evicted; the newest remain.
        assert _md5("text-0") not in dedup._seen
        assert _md5(f"text-{_ONCE_DEDUP_MAX_ENTRIES + 49}") in dedup._seen


class TestChimeDedup:
    """ChimeDedup is the renamed AudioDedup, simplified for the chime path."""

    def test_first_chime_plays(self) -> None:
        dedup = ChimeDedup()
        assert dedup.should_play("tests-pass") is True

    def test_duplicate_chime_within_window_dropped(self) -> None:
        dedup = ChimeDedup()
        assert dedup.should_play("tests-pass") is True
        assert dedup.should_play("tests-pass") is False

    def test_different_signal_not_dropped(self) -> None:
        dedup = ChimeDedup()
        assert dedup.should_play("tests-pass") is True
        assert dedup.should_play("lint-fail") is True

    def test_chime_dedup_after_window_plays_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dedup = ChimeDedup(window=5.0)
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.dedup.time.monotonic", lambda: clock[0])
        assert dedup.should_play("tests-pass") is True
        clock[0] = 1010.0  # past 5s window
        assert dedup.should_play("tests-pass") is True
