"""Tests for Signal and SignalLog (src/punt_vox/signal.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from punt_vox.signal import Signal, SignalLog

# ---------------------------------------------------------------------------
# Signal tests
# ---------------------------------------------------------------------------


class TestSignal:
    def test_to_token_with_timestamp(self) -> None:
        s = Signal(signal_type="tests-pass", timestamp="14:32")
        assert s.to_token() == "tests-pass@14:32"

    def test_to_token_empty_timestamp(self) -> None:
        s = Signal(signal_type="lint-fail", timestamp="")
        assert s.to_token() == "lint-fail@"

    def test_from_token_standard(self) -> None:
        s = Signal.from_token("tests-pass@14:32")
        assert s.signal_type == "tests-pass"
        assert s.timestamp == "14:32"

    def test_from_token_no_at_sign(self) -> None:
        s = Signal.from_token("git-commit")
        assert s.signal_type == "git-commit"
        assert s.timestamp == ""

    def test_from_token_strips_whitespace(self) -> None:
        s = Signal.from_token("  lint-pass @ 09:00 ")
        assert s.signal_type == "lint-pass"
        assert s.timestamp == "09:00"

    def test_from_token_only_at_sign(self) -> None:
        with pytest.raises(ValueError, match="empty type"):
            Signal.from_token("@")

    def test_from_token_extra_at_sign_kept_in_timestamp(self) -> None:
        # partition takes only the first @; remainder stays in timestamp
        s = Signal.from_token("foo@14:32@extra")
        assert s.signal_type == "foo"
        assert s.timestamp == "14:32@extra"

    def test_from_token_empty_string(self) -> None:
        with pytest.raises(ValueError, match="empty token"):
            Signal.from_token("")

    def test_from_token_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="empty token"):
            Signal.from_token("   ")

    def test_from_token_roundtrip(self) -> None:
        original = Signal(signal_type="pr-created", timestamp="17:45")
        assert Signal.from_token(original.to_token()) == original

    def test_now_uses_current_time(self) -> None:
        with patch("punt_vox.signal.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "10:30"
            s = Signal.now("tests-pass")
        assert s.signal_type == "tests-pass"
        assert s.timestamp == "10:30"

    def test_frozen_immutable(self) -> None:
        s = Signal(signal_type="lint-pass", timestamp="12:00")
        with pytest.raises((AttributeError, TypeError)):
            s.signal_type = "lint-fail"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = Signal(signal_type="tests-pass", timestamp="14:32")
        b = Signal(signal_type="tests-pass", timestamp="14:32")
        assert a == b

    def test_inequality_different_type(self) -> None:
        a = Signal(signal_type="tests-pass", timestamp="14:32")
        b = Signal(signal_type="tests-fail", timestamp="14:32")
        assert a != b


# ---------------------------------------------------------------------------
# SignalLog tests
# ---------------------------------------------------------------------------


class TestSignalLog:
    def test_empty_log_has_len_zero(self) -> None:
        log = SignalLog()
        assert len(log) == 0

    def test_append_increases_len(self) -> None:
        log = SignalLog()
        log.append(Signal(signal_type="tests-pass", timestamp="12:00"))
        assert len(log) == 1

    def test_append_evicts_oldest_at_capacity(self) -> None:
        log = SignalLog(max_entries=3)
        for i in range(3):
            log.append(Signal(signal_type=f"sig-{i}", timestamp="00:00"))
        # Adding a 4th evicts sig-0
        log.append(Signal(signal_type="sig-3", timestamp="00:00"))
        assert len(log) == 3
        tokens = log.serialize()
        assert "sig-0" not in tokens
        assert "sig-3" in tokens

    def test_append_uses_max_entries_invariant(self) -> None:
        """deserialize + append must never exceed MAX_ENTRIES."""
        raw = ",".join(f"old-{i}@00:00" for i in range(SignalLog.MAX_ENTRIES))
        log = SignalLog.deserialize(raw)
        log.append(Signal(signal_type="new-signal", timestamp="01:00"))
        assert len(log) == SignalLog.MAX_ENTRIES
        assert "old-0@00:00" not in log.serialize()
        assert "new-signal" in log.serialize()

    def test_max_entries_class_var(self) -> None:
        assert SignalLog.MAX_ENTRIES == 20

    def test_custom_max_entries(self) -> None:
        log = SignalLog(max_entries=5)
        for i in range(6):
            log.append(Signal(signal_type=f"s-{i}", timestamp="00:00"))
        assert len(log) == 5

    def test_counts_empty(self) -> None:
        assert SignalLog().counts() == {}

    def test_counts_single_type(self) -> None:
        log = SignalLog()
        log.append(Signal(signal_type="tests-pass", timestamp="12:00"))
        log.append(Signal(signal_type="tests-pass", timestamp="12:01"))
        assert log.counts() == {"tests-pass": 2}

    def test_counts_mixed(self) -> None:
        log = SignalLog()
        log.append(Signal(signal_type="tests-pass", timestamp="12:00"))
        log.append(Signal(signal_type="lint-fail", timestamp="12:01"))
        log.append(Signal(signal_type="tests-pass", timestamp="12:02"))
        counts = log.counts()
        assert counts["tests-pass"] == 2
        assert counts["lint-fail"] == 1

    def test_last_returns_most_recent(self) -> None:
        log = SignalLog()
        for i in range(5):
            log.append(Signal(signal_type=f"s-{i}", timestamp="00:00"))
        last = log.last(2)
        assert len(last) == 2
        assert last[0].signal_type == "s-3"
        assert last[1].signal_type == "s-4"

    def test_last_clamps_to_available(self) -> None:
        log = SignalLog()
        log.append(Signal(signal_type="only", timestamp="00:00"))
        assert len(log.last(10)) == 1

    def test_last_zero_returns_empty(self) -> None:
        log = SignalLog.deserialize("tests-pass@12:00,lint-fail@13:00")
        assert log.last(0) == []

    def test_serialize_empty(self) -> None:
        assert SignalLog().serialize() == ""

    def test_serialize_single(self) -> None:
        log = SignalLog()
        log.append(Signal(signal_type="tests-pass", timestamp="14:32"))
        assert log.serialize() == "tests-pass@14:32"

    def test_serialize_multiple(self) -> None:
        log = SignalLog()
        log.append(Signal(signal_type="lint-pass", timestamp="11:00"))
        log.append(Signal(signal_type="tests-pass", timestamp="11:01"))
        assert log.serialize() == "lint-pass@11:00,tests-pass@11:01"

    def test_deserialize_empty_string(self) -> None:
        log = SignalLog.deserialize("")
        assert len(log) == 0

    def test_deserialize_single_token(self) -> None:
        log = SignalLog.deserialize("tests-pass@14:32")
        assert len(log) == 1
        assert log.last(1)[0] == Signal(signal_type="tests-pass", timestamp="14:32")

    def test_deserialize_multiple_tokens(self) -> None:
        log = SignalLog.deserialize("lint-pass@11:00,tests-pass@11:01")
        assert len(log) == 2

    def test_deserialize_roundtrip(self) -> None:
        raw = "tests-fail@01:00,tests-pass@02:00,git-push-ok@03:00"
        assert SignalLog.deserialize(raw).serialize() == raw

    def test_deserialize_overlong_evicts(self) -> None:
        """Deserializing more than max_entries tokens must evict oldest."""
        tokens = ",".join(f"s-{i}@00:00" for i in range(5))
        log = SignalLog.deserialize(tokens, max_entries=3)
        assert len(log) == 3
        assert "s-0" not in log.serialize()
        assert "s-4" in log.serialize()

    def test_deserialize_uses_append_not_direct(self) -> None:
        """SignalLog.deserialize must call append() so MAX_ENTRIES applies."""
        # If deserialize bypassed append(), this would hold 25 entries.
        tokens = ",".join(f"s-{i}@00:00" for i in range(25))
        log = SignalLog.deserialize(tokens, max_entries=20)
        assert len(log) == 20

    def test_deserialize_skips_malformed_token(self) -> None:
        log = SignalLog.deserialize("tests-pass@12:00,@14:32,lint-fail@13:00")
        assert len(log) == 2
        types = [s.signal_type for s in log.last(2)]
        assert "tests-pass" in types
        assert "lint-fail" in types

    def test_deserialize_all_malformed_returns_empty(self) -> None:
        log = SignalLog.deserialize("@,@14:32")
        assert len(log) == 0


# ---------------------------------------------------------------------------
# SignalLog.resolve_tags tests
# ---------------------------------------------------------------------------


class TestSignalLogResolveTags:
    def test_empty_returns_calm(self) -> None:
        assert SignalLog().resolve_tags() == "[calm]"

    def test_single_pass_returns_calm(self) -> None:
        assert SignalLog.deserialize("tests-pass@12:00").resolve_tags() == "[calm]"

    def test_many_passes_no_fails_returns_excited(self) -> None:
        signals = ",".join(f"tests-pass@{i:02d}:00" for i in range(5))
        assert SignalLog.deserialize(signals).resolve_tags() == "[excited]"

    def test_recovery_arc_returns_relieved(self) -> None:
        signals = "tests-fail@01:00,tests-fail@02:00,tests-pass@03:00"
        assert SignalLog.deserialize(signals).resolve_tags() == "[relieved]"

    def test_mostly_failing_returns_frustrated(self) -> None:
        signals = "tests-fail@01:00,tests-fail@02:00,lint-fail@03:00"
        assert "[frustrated]" in SignalLog.deserialize(signals).resolve_tags()

    def test_shipped_clean_returns_satisfied(self) -> None:
        signals = "tests-pass@01:00,git-push-ok@02:00"
        assert "[satisfied]" in SignalLog.deserialize(signals).resolve_tags()

    def test_shipped_after_struggle(self) -> None:
        signals = "tests-fail@01:00,tests-pass@02:00,git-push-ok@03:00"
        tags = SignalLog.deserialize(signals).resolve_tags()
        assert tags == "[relieved] [satisfied]"

    def test_pr_created_returns_satisfied(self) -> None:
        signals = "tests-pass@01:00,pr-created@02:00"
        assert "[satisfied]" in SignalLog.deserialize(signals).resolve_tags()

    def test_push_after_fails_returns_relieved_satisfied(self) -> None:
        signals = "tests-fail@01:00,tests-pass@02:00,git-push-ok@03:00"
        tags = SignalLog.deserialize(signals).resolve_tags()
        assert tags == "[relieved] [satisfied]"
