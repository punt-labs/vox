"""Tests for session event watcher."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from punt_vox.watcher import (
    _SIGNAL_CHIMES,  # pyright: ignore[reportPrivateUsage]
    SessionEvent,
    SessionWatcher,
    _extract_tool_result_text,  # pyright: ignore[reportPrivateUsage]
    _find_session_jsonl,  # pyright: ignore[reportPrivateUsage]
    _resolve_assets_dir,  # pyright: ignore[reportPrivateUsage]
    classify_output,
    derive_session_dir,
    make_notification_consumer,
    resolve_chime_path,
)

del TYPE_CHECKING  # unused after removing pytest guard


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassifyOutput:
    """Pattern matching for bash output classification."""

    def test_tests_pass_pytest(self) -> None:
        assert classify_output("5 passed in 1.23s") == "tests-pass"

    def test_tests_pass_ok(self) -> None:
        assert classify_output("test ok") == "tests-pass"

    def test_tests_pass_checkmark(self) -> None:
        assert classify_output("✓ 42 passed") == "tests-pass"

    def test_tests_fail_failed(self) -> None:
        assert classify_output("FAILED tests/test_foo.py") == "tests-fail"

    def test_tests_fail_assertion(self) -> None:
        assert classify_output("AssertionError: expected 1") == "tests-fail"

    def test_tests_fail_errors_during_collection(self) -> None:
        assert classify_output("2 errors during collection") == "tests-fail"

    def test_lint_pass_all_checks(self) -> None:
        assert classify_output("All checks passed!") == "lint-pass"

    def test_lint_pass_zero_errors(self) -> None:
        assert classify_output("0 errors found") == "lint-pass"

    def test_lint_fail(self) -> None:
        assert classify_output("Found 3 errors") == "lint-fail"

    def test_git_push_ok_up_to_date(self) -> None:
        assert classify_output("Everything up-to-date") == "git-push-ok"

    def test_git_push_ok_arrow(self) -> None:
        assert classify_output("abc123 -> refs/heads/main") == "git-push-ok"

    def test_merge_conflict(self) -> None:
        assert classify_output("CONFLICT (content): Merge conflict") == "merge-conflict"

    def test_unrecognized_returns_none(self) -> None:
        assert classify_output("Hello world") is None

    def test_empty_string(self) -> None:
        assert classify_output("") is None


# ---------------------------------------------------------------------------
# JSONL extraction
# ---------------------------------------------------------------------------


class TestExtractToolResultText:
    """JSONL parsing for tool result content extraction."""

    def test_direct_tool_result_string_content(self) -> None:
        data: dict[str, object] = {
            "type": "tool_result",
            "content": "5 passed in 1.23s",
        }
        assert _extract_tool_result_text(data) == "5 passed in 1.23s"

    def test_direct_tool_result_structured_content(self) -> None:
        data: dict[str, object] = {
            "type": "tool_result",
            "content": [{"type": "text", "text": "5 passed"}],
        }
        assert _extract_tool_result_text(data) == "5 passed"

    def test_message_with_tool_result_block(self) -> None:
        data: dict[str, object] = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "123",
                    "content": "output text",
                },
            ],
        }
        assert _extract_tool_result_text(data) == "output text"

    def test_message_without_tool_result(self) -> None:
        data: dict[str, object] = {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello"}],
        }
        assert _extract_tool_result_text(data) is None

    def test_empty_content_list(self) -> None:
        data: dict[str, object] = {"role": "user", "content": []}
        assert _extract_tool_result_text(data) is None

    def test_non_list_content(self) -> None:
        data: dict[str, object] = {"role": "user", "content": "string"}
        assert _extract_tool_result_text(data) is None

    def test_no_content_field(self) -> None:
        data: dict[str, object] = {"type": "other"}
        assert _extract_tool_result_text(data) is None

    def test_multiple_tool_results(self) -> None:
        data: dict[str, object] = {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "first"},
                {"type": "tool_result", "content": "second"},
            ],
        }
        assert _extract_tool_result_text(data) == "first\nsecond"


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


class TestSessionDiscovery:
    """Session directory derivation and JSONL file selection."""

    def testderive_session_dir(self) -> None:
        result = derive_session_dir(Path("/Users/alice/project"))
        expected = Path.home() / ".claude" / "projects" / "-Users-alice-project"
        assert result == expected

    def test_find_session_jsonl_most_recent(self, tmp_path: Path) -> None:
        old = tmp_path / "old.jsonl"
        old.write_text("{}\n")
        os.utime(old, (1000, 1000))

        new = tmp_path / "new.jsonl"
        new.write_text("{}\n")
        os.utime(new, (2000, 2000))

        assert _find_session_jsonl(tmp_path) == new

    def test_find_session_jsonl_empty_dir(self, tmp_path: Path) -> None:
        assert _find_session_jsonl(tmp_path) is None

    def test_find_session_jsonl_no_jsonl_files(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("hi")
        assert _find_session_jsonl(tmp_path) is None

    def test_find_session_jsonl_ignores_subdirectories(self, tmp_path: Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "nested.jsonl").write_text("{}\n")
        assert _find_session_jsonl(tmp_path) is None

    def test_find_session_jsonl_nonexistent_dir(self, tmp_path: Path) -> None:
        assert _find_session_jsonl(tmp_path / "nonexistent") is None


# ---------------------------------------------------------------------------
# Notification consumer
# ---------------------------------------------------------------------------


class TestNotificationConsumer:
    """Notification consumer config gating and throttle behavior."""

    def test_skips_when_notify_not_c(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.md"
        config_path.write_text('---\nnotify: "y"\n---\n')
        consumer = make_notification_consumer(config_path=config_path)
        event = SessionEvent(
            signal="tests-pass", timestamp=time.time(), source_text="ok"
        )

        with patch("punt_vox.watcher._announce_voice") as mock_voice:
            consumer(event)
            mock_voice.assert_not_called()

    def test_fires_when_notify_c_speak_y(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.md"
        config_path.write_text('---\nnotify: "c"\nspeak: "y"\n---\n')
        consumer = make_notification_consumer(
            config_path=config_path, throttle_seconds=0.0
        )
        event = SessionEvent(
            signal="tests-pass", timestamp=time.time(), source_text="ok"
        )

        with patch("punt_vox.watcher._announce_voice") as mock_voice:
            consumer(event)
            mock_voice.assert_called_once_with(event)

    def test_chime_mode_when_speak_n(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.md"
        config_path.write_text('---\nnotify: "c"\nspeak: "n"\n---\n')
        consumer = make_notification_consumer(
            config_path=config_path, throttle_seconds=0.0
        )
        event = SessionEvent(
            signal="tests-pass", timestamp=time.time(), source_text="ok"
        )

        with patch("punt_vox.watcher._announce_chime") as mock_chime:
            consumer(event)
            mock_chime.assert_called_once_with("tests-pass", None)

    def test_chime_mode_passes_vibe(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.md"
        config_path.write_text('---\nnotify: "c"\nspeak: "n"\nvibe: "happy"\n---\n')
        consumer = make_notification_consumer(
            config_path=config_path, throttle_seconds=0.0
        )
        event = SessionEvent(
            signal="tests-pass", timestamp=time.time(), source_text="ok"
        )

        with patch("punt_vox.watcher._announce_chime") as mock_chime:
            consumer(event)
            mock_chime.assert_called_once_with("tests-pass", "happy")

    @pytest.mark.skipif(
        os.environ.get("CI") == "true",
        reason="Flaky on CI — _announce_voice mock not called (tts-2sj)",
    )
    def test_throttles_same_signal(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.md"
        config_path.write_text('---\nnotify: "c"\nspeak: "y"\n---\n')
        consumer = make_notification_consumer(
            config_path=config_path, throttle_seconds=100.0
        )
        event = SessionEvent(
            signal="tests-pass", timestamp=time.time(), source_text="ok"
        )

        with patch("punt_vox.watcher._announce_voice") as mock_voice:
            consumer(event)  # fires
            consumer(event)  # throttled
            assert mock_voice.call_count == 1

    @pytest.mark.skipif(
        os.environ.get("CI") == "true",
        reason="Flaky on CI — _announce_voice mock not called (tts-2sj)",
    )
    def test_different_signals_not_throttled(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.md"
        config_path.write_text('---\nnotify: "c"\nspeak: "y"\n---\n')
        consumer = make_notification_consumer(
            config_path=config_path, throttle_seconds=100.0
        )
        e1 = SessionEvent(signal="tests-pass", timestamp=time.time(), source_text="ok")
        e2 = SessionEvent(signal="lint-pass", timestamp=time.time(), source_text="ok")

        with patch("punt_vox.watcher._announce_voice") as mock_voice:
            consumer(e1)
            consumer(e2)
            assert mock_voice.call_count == 2

    def test_skips_when_no_config_file(self, tmp_path: Path) -> None:
        consumer = make_notification_consumer(config_path=tmp_path / "missing.md")
        event = SessionEvent(
            signal="tests-pass", timestamp=time.time(), source_text="ok"
        )

        with patch("punt_vox.watcher._announce_voice") as mock_voice:
            consumer(event)
            mock_voice.assert_not_called()


# ---------------------------------------------------------------------------
# Chime path resolution
# ---------------------------------------------------------------------------


class TestResolveChimePath:
    """Chime file discovery from env var and source tree."""

    def test_default_returns_chime_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        chime = assets / "chime_done.mp3"
        chime.write_bytes(b"fake")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        assert resolve_chime_path() == chime

    def test_signal_specific_chime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "chime_done.mp3").write_bytes(b"fallback")
        signal_chime = assets / "chime_tests_pass.mp3"
        signal_chime.write_bytes(b"pass-chime")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        assert resolve_chime_path("tests-pass") == signal_chime

    def test_signal_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        fallback = assets / "chime_done.mp3"
        fallback.write_bytes(b"fallback")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        # No signal-specific file for lint-pass
        assert resolve_chime_path("lint-pass") == fallback

    def test_all_signals_have_mapping(self) -> None:
        """Every classified signal has a chime filename entry."""
        from punt_vox.hooks import (
            _SIGNAL_PATTERNS,  # pyright: ignore[reportPrivateUsage]
        )

        signal_names = {name for name, _ in _SIGNAL_PATTERNS}
        assert signal_names == set(_SIGNAL_CHIMES.keys())

    def test_returns_none_when_no_assets_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        fake = tmp_path / "src" / "punt_vox" / "fake.py"
        monkeypatch.setattr("punt_vox.watcher.__file__", str(fake))
        assert resolve_chime_path() is None
        assert resolve_chime_path("tests-pass") is None

    def test_mood_specific_signal_chime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "chime_done.mp3").write_bytes(b"fallback")
        (assets / "chime_tests_pass.mp3").write_bytes(b"neutral")
        bright = assets / "chime_tests_pass_bright.mp3"
        bright.write_bytes(b"bright")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        assert resolve_chime_path("tests-pass", mood="bright") == bright

    def test_mood_falls_back_to_neutral_signal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "chime_done.mp3").write_bytes(b"fallback")
        neutral = assets / "chime_tests_pass.mp3"
        neutral.write_bytes(b"neutral")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        # No bright variant → falls back to neutral signal chime
        assert resolve_chime_path("tests-pass", mood="bright") == neutral

    def test_mood_falls_back_to_mood_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "chime_done.mp3").write_bytes(b"neutral-done")
        mood_done = assets / "chime_done_dark.mp3"
        mood_done.write_bytes(b"dark-done")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        # Unknown signal, no signal chime → falls back to mood-specific done
        assert resolve_chime_path("unknown-signal", mood="dark") == mood_done

    def test_mood_neutral_skips_mood_suffix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        neutral = assets / "chime_tests_pass.mp3"
        neutral.write_bytes(b"neutral")
        (assets / "chime_done.mp3").write_bytes(b"fallback")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        # Neutral mood never looks for _neutral suffix
        assert resolve_chime_path("tests-pass", mood="neutral") == neutral

    def test_resolve_assets_dir_from_plugin_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
        assert _resolve_assets_dir() == assets

    def test_resolve_assets_dir_from_source_tree(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        # Use the real source tree — assets/ exists in the repo
        result = _resolve_assets_dir()
        assert result is not None
        assert result.name == "assets"


# ---------------------------------------------------------------------------
# Watcher lifecycle
# ---------------------------------------------------------------------------


class TestWatcherLifecycle:
    """Thread start/stop and line processing."""

    def test_start_and_stop(self, tmp_path: Path) -> None:
        watcher = SessionWatcher(session_dir=tmp_path, consumers=[], poll_interval=0.05)
        watcher.start()
        assert watcher.running
        watcher.stop()
        assert not watcher.running

    def test_start_is_idempotent(self, tmp_path: Path) -> None:
        watcher = SessionWatcher(session_dir=tmp_path, consumers=[], poll_interval=0.05)
        watcher.start()
        watcher.start()  # second call is a no-op
        assert watcher.running
        watcher.stop()

    def test_processes_new_lines(self, tmp_path: Path) -> None:
        events: list[SessionEvent] = []
        received = threading.Event()

        def _collecting_consumer(event: SessionEvent) -> None:
            events.append(event)
            received.set()

        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text("")

        watcher = SessionWatcher(
            session_dir=tmp_path,
            consumers=[_collecting_consumer],
            poll_interval=0.05,
        )
        watcher.start()

        # Wait for watcher to discover and latch onto the file
        assert watcher._file_latched.wait(timeout=2.0)  # pyright: ignore[reportPrivateUsage]

        # Append a new line with a classifiable tool result
        line = json.dumps({"type": "tool_result", "content": "5 passed in 1.23s"})
        with jsonl_path.open("a") as f:
            f.write(line + "\n")

        # Wait for event delivery
        received.wait(timeout=2.0)
        watcher.stop()

        assert len(events) == 1
        assert events[0].signal == "tests-pass"

    def test_skips_malformed_json(self, tmp_path: Path) -> None:
        events: list[SessionEvent] = []
        received = threading.Event()

        def _collecting_consumer(event: SessionEvent) -> None:
            events.append(event)
            received.set()

        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text("")

        watcher = SessionWatcher(
            session_dir=tmp_path,
            consumers=[_collecting_consumer],
            poll_interval=0.05,
        )
        watcher.start()
        assert watcher._file_latched.wait(timeout=2.0)  # pyright: ignore[reportPrivateUsage]

        with jsonl_path.open("a") as f:
            f.write("not json\n")
            f.write(json.dumps({"type": "tool_result", "content": "5 passed"}) + "\n")

        received.wait(timeout=2.0)
        watcher.stop()

        assert len(events) == 1

    def test_skips_unclassifiable_output(self, tmp_path: Path) -> None:
        events: list[SessionEvent] = []
        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text("")

        watcher = SessionWatcher(
            session_dir=tmp_path,
            consumers=[events.append],
            poll_interval=0.05,
        )
        watcher.start()
        assert watcher._file_latched.wait(timeout=2.0)  # pyright: ignore[reportPrivateUsage]

        with jsonl_path.open("a") as f:
            f.write(
                json.dumps({"type": "tool_result", "content": "hello world"}) + "\n"
            )

        # No event expected — wait a few poll cycles then check
        time.sleep(0.15)
        watcher.stop()

        assert len(events) == 0

    def test_no_jsonl_does_not_crash(self, tmp_path: Path) -> None:
        watcher = SessionWatcher(session_dir=tmp_path, consumers=[], poll_interval=0.05)
        watcher.start()
        # Give the thread time to enter the run loop
        time.sleep(0.1)
        assert watcher.running
        watcher.stop()

    def test_consumer_exception_does_not_stop_watcher(self, tmp_path: Path) -> None:
        call_count = 0
        called = threading.Event()

        def bad_consumer(event: SessionEvent) -> None:
            nonlocal call_count
            call_count += 1
            called.set()
            msg = "boom"
            raise RuntimeError(msg)

        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text("")

        watcher = SessionWatcher(
            session_dir=tmp_path,
            consumers=[bad_consumer],
            poll_interval=0.05,
        )
        watcher.start()
        assert watcher._file_latched.wait(timeout=2.0)  # pyright: ignore[reportPrivateUsage]

        line = json.dumps({"type": "tool_result", "content": "5 passed in 1.23s"})
        with jsonl_path.open("a") as f:
            f.write(line + "\n")

        called.wait(timeout=2.0)
        assert watcher.running
        watcher.stop()
        assert call_count >= 1

    def test_start_restarts_dead_thread(self, tmp_path: Path) -> None:
        """start() restarts if the previous thread died."""
        watcher = SessionWatcher(session_dir=tmp_path, consumers=[], poll_interval=0.05)

        # Simulate a dead thread reference (thread finished but not cleared
        # because of a join timeout scenario in production)
        dead_thread = threading.Thread(target=lambda: None)
        dead_thread.start()
        dead_thread.join()
        watcher._thread = dead_thread  # pyright: ignore[reportPrivateUsage]

        # start() should detect the dead thread and restart
        watcher.start()
        assert watcher.running
        watcher.stop()

    def test_handles_file_truncation(self, tmp_path: Path) -> None:
        """Watcher resets file position when file is truncated."""
        events: list[SessionEvent] = []
        received = threading.Event()

        def _collecting_consumer(event: SessionEvent) -> None:
            events.append(event)
            received.set()

        jsonl_path = tmp_path / "session.jsonl"
        # Start with some content so the watcher latches at a non-zero offset
        initial_line = json.dumps({"type": "tool_result", "content": "hello"})
        jsonl_path.write_text(initial_line + "\n")

        watcher = SessionWatcher(
            session_dir=tmp_path,
            consumers=[_collecting_consumer],
            poll_interval=0.05,
        )
        watcher.start()
        assert watcher._file_latched.wait(timeout=2.0)  # pyright: ignore[reportPrivateUsage]

        # Wait one poll cycle so the watcher reads the initial position
        time.sleep(0.1)

        # Truncate the file (simulates copytruncate)
        jsonl_path.write_text("")

        # Wait for watcher to detect truncation and reset
        time.sleep(0.15)

        # Write new content after truncation
        new_line = json.dumps({"type": "tool_result", "content": "5 passed in 1s"})
        with jsonl_path.open("a") as f:
            f.write(new_line + "\n")

        received.wait(timeout=2.0)
        watcher.stop()

        assert len(events) >= 1
        assert events[0].signal == "tests-pass"
