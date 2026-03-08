"""Tests for hook dispatchers (src/punt_vox/hooks.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

from punt_vox.config import VoxConfig
from punt_vox.hooks import (
    PRE_COMPACT_PHRASES,
    STOP_PHRASES,
    classify_signal,
    handle_notification,
    handle_pre_compact,
    handle_stop,
    resolve_chime,
    resolve_tags_from_signals,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(
    *,
    notify: str = "y",
    speak: str = "y",
    vibe: str | None = None,
    vibe_signals: str | None = "tests-pass@12:00",
    voice: str | None = None,
) -> VoxConfig:
    return VoxConfig(
        notify=notify,
        speak=speak,
        voice_enabled="true",
        vibe_mode="auto",
        voice=voice,
        vibe=vibe,
        vibe_tags=None,
        vibe_signals=vibe_signals,
    )


# ---------------------------------------------------------------------------
# classify_signal tests
# ---------------------------------------------------------------------------


class TestClassifySignal:
    def test_tests_pass(self) -> None:
        assert classify_signal(0, "5 passed in 1.2s") == "tests-pass"

    def test_tests_pass_checkmark(self) -> None:
        assert classify_signal(0, "\u2713 3 passed") == "tests-pass"

    def test_tests_fail(self) -> None:
        assert classify_signal(1, "FAILED tests/test_foo.py") == "tests-fail"

    def test_lint_fail(self) -> None:
        assert classify_signal(1, "Found 3 errors") == "lint-fail"

    def test_lint_pass(self) -> None:
        assert classify_signal(0, "0 errors, 0 warnings") == "lint-pass"

    def test_merge_conflict(self) -> None:
        assert classify_signal(1, "CONFLICT (content): file.py") == "merge-conflict"

    def test_git_push_ok(self) -> None:
        assert classify_signal(0, "Everything up-to-date") == "git-push-ok"

    def test_git_push_ok_branch(self) -> None:
        assert classify_signal(0, "abc1234..def5678 -> main") == "git-push-ok"

    def test_git_commit(self) -> None:
        assert classify_signal(0, "[main abc1234] fix: thing") == "git-commit"

    def test_pr_created(self) -> None:
        assert classify_signal(0, "https://github.com/org/repo/pull/42") == "pr-created"

    def test_cmd_fail_generic(self) -> None:
        assert classify_signal(1, "some unknown output") == "cmd-fail"

    def test_no_match_success(self) -> None:
        assert classify_signal(0, "some unknown output") is None

    def test_empty_output(self) -> None:
        assert classify_signal(0, "") is None

    def test_none_exit_code_no_match(self) -> None:
        assert classify_signal(None, "some output") is None

    def test_git_commit_not_first_line(self) -> None:
        # re.MULTILINE: ^ matches line starts, not just string start
        output = "some preamble\n[main abc1234] fix: thing\n"
        assert classify_signal(0, output) == "git-commit"

    def test_negative_exit_code_string(self) -> None:
        # Negative exit codes passed as strings should still classify
        assert classify_signal(-1, "some unknown output") == "cmd-fail"


# ---------------------------------------------------------------------------
# handle_stop tests
# ---------------------------------------------------------------------------


class TestHandleStop:
    def test_notify_disabled(self) -> None:
        config = _make_config(notify="n")
        assert handle_stop({}, config) is None

    def test_stop_hook_active(self) -> None:
        config = _make_config()
        assert handle_stop({"stop_hook_active": True}, config) is None

    def test_no_signals(self) -> None:
        config = _make_config(vibe_signals=None)
        assert handle_stop({}, config) is None

    def test_empty_signals(self) -> None:
        config = _make_config(vibe_signals="")
        assert handle_stop({}, config) is None

    @patch("punt_vox.hooks._enqueue_audio")
    def test_chime_mode(self, mock_enqueue: object) -> None:
        config = _make_config(speak="n")
        result = handle_stop({}, config)
        assert result is None

    @patch("punt_vox.hooks.write_fields")
    @patch("punt_vox.hooks.resolve_config_path")
    def test_voice_mode_blocks_clean_reason(
        self, _mock_path: MagicMock, _mock_write: MagicMock
    ) -> None:
        config = _make_config()
        result = handle_stop({}, config)
        assert result is not None
        assert result["decision"] == "block"
        reason = str(result["reason"])
        # Reason is just the ♪ phrase — no data appended
        assert any(reason == phrase for phrase in STOP_PHRASES)
        assert "|" not in reason
        assert "vibe_tags" not in reason
        assert "vibe_signals" not in reason

    @patch("punt_vox.hooks.write_fields")
    @patch("punt_vox.hooks.resolve_config_path")
    def test_auto_mode_writes_tags_and_clears_signals(
        self, mock_path: MagicMock, mock_write: MagicMock
    ) -> None:
        config = _make_config(vibe_signals="tests-pass@01:00,git-push-ok@02:00")
        handle_stop({}, config)
        assert mock_write.call_count == 1
        assert mock_write.call_args == call(
            {"vibe_tags": "[satisfied]", "vibe_signals": ""},
            mock_path.return_value,
        )

    @patch("punt_vox.hooks.write_fields")
    @patch("punt_vox.hooks.resolve_config_path")
    def test_continuous_mode_blocks(
        self, _mock_path: MagicMock, _mock_write: MagicMock
    ) -> None:
        config = _make_config(notify="c")
        result = handle_stop({}, config)
        assert result is not None
        assert result["decision"] == "block"

    def test_manual_vibe_skips_config_write(self) -> None:
        config = VoxConfig(
            notify="y",
            speak="y",
            voice_enabled="true",
            vibe_mode="manual",
            voice=None,
            vibe=None,
            vibe_tags="[excited] [warm]",
            vibe_signals="tests-pass@12:00",
        )
        with patch("punt_vox.hooks.write_field") as mock_write:
            result = handle_stop({}, config)
        assert result is not None
        # Manual mode with existing tags — no write needed
        mock_write.assert_not_called()

    def test_vibe_off_skips_config_write(self) -> None:
        config = VoxConfig(
            notify="y",
            speak="y",
            voice_enabled="true",
            vibe_mode="off",
            voice=None,
            vibe=None,
            vibe_tags=None,
            vibe_signals="tests-pass@12:00",
        )
        with patch("punt_vox.hooks.write_field") as mock_write:
            result = handle_stop({}, config)
        assert result is not None
        assert result["decision"] == "block"
        # Vibe off — must not write tags to config
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_tags_from_signals tests
# ---------------------------------------------------------------------------


class TestResolveTagsFromSignals:
    def test_empty_signals(self) -> None:
        assert resolve_tags_from_signals("") == "[calm]"

    def test_single_pass(self) -> None:
        assert resolve_tags_from_signals("tests-pass@12:00") == "[calm]"

    def test_many_passes_no_fails(self) -> None:
        signals = ",".join(f"tests-pass@{i:02d}:00" for i in range(5))
        assert resolve_tags_from_signals(signals) == "[excited]"

    def test_recovery_arc(self) -> None:
        signals = "tests-fail@01:00,tests-fail@02:00,tests-pass@03:00"
        assert resolve_tags_from_signals(signals) == "[relieved]"

    def test_mostly_failing(self) -> None:
        signals = "tests-fail@01:00,tests-fail@02:00,lint-fail@03:00"
        assert "[frustrated]" in resolve_tags_from_signals(signals)

    def test_shipped_clean(self) -> None:
        signals = "tests-pass@01:00,git-push-ok@02:00"
        assert "[satisfied]" in resolve_tags_from_signals(signals)

    def test_shipped_after_struggle(self) -> None:
        signals = "tests-fail@01:00,tests-pass@02:00,git-push-ok@03:00"
        tags = resolve_tags_from_signals(signals)
        assert "[relieved]" in tags or "[satisfied]" in tags

    def test_pr_created(self) -> None:
        signals = "tests-pass@01:00,pr-created@02:00"
        assert "[satisfied]" in resolve_tags_from_signals(signals)


# ---------------------------------------------------------------------------
# resolve_chime tests
# ---------------------------------------------------------------------------


class TestResolveChime:
    def test_neutral_mood_returns_signal_chime(self) -> None:
        chime = resolve_chime("done", None)
        assert chime.name == "chime_done.mp3"

    def test_bright_mood_returns_mood_chime(self) -> None:
        chime = resolve_chime("done", "happy and excited")
        assert chime.name == "chime_done_bright.mp3"

    def test_dark_mood_returns_mood_chime(self) -> None:
        chime = resolve_chime("done", "frustrated")
        assert chime.name == "chime_done_dark.mp3"

    def test_fallback_to_neutral_signal(self) -> None:
        # Signal that doesn't have mood variants falls to neutral
        chime = resolve_chime("done", None)
        assert "chime_done.mp3" in chime.name

    def test_unknown_signal_falls_to_done(self) -> None:
        chime = resolve_chime("nonexistent_signal", None)
        assert chime.name == "chime_done.mp3"

    def test_prompt_chime(self) -> None:
        chime = resolve_chime("prompt", None)
        assert chime.name == "chime_prompt.mp3"

    def test_hyphenated_signal_resolves_underscore_file(self) -> None:
        # Signal "tests-pass" should find "chime_tests_pass.mp3"
        chime = resolve_chime("tests-pass", None)
        assert chime.name == "chime_tests_pass.mp3"


# ---------------------------------------------------------------------------
# handle_notification tests
# ---------------------------------------------------------------------------


class TestHandleNotification:
    @patch("punt_vox.hooks._enqueue_audio")
    def test_notify_disabled_does_nothing(self, mock_enqueue: object) -> None:
        config = _make_config(notify="n")
        handle_notification({"notification_type": "permission_prompt"}, config)
        # Should not enqueue anything when notify=n
        from unittest.mock import MagicMock

        assert isinstance(mock_enqueue, MagicMock)
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._enqueue_audio")
    def test_chime_mode_plays_chime(self, mock_enqueue: object) -> None:
        config = _make_config(speak="n")
        handle_notification({"notification_type": "permission_prompt"}, config)
        from unittest.mock import MagicMock

        assert isinstance(mock_enqueue, MagicMock)
        mock_enqueue.assert_called_once()
        chime_path = mock_enqueue.call_args[0][0]
        assert "chime_prompt" in chime_path.name

    @patch("punt_vox.hooks.subprocess.run")
    def test_voice_mode_calls_vox_unmute(self, mock_run: object) -> None:
        config = _make_config(speak="y", voice="matilda")
        handle_notification({"notification_type": "permission_prompt"}, config)
        from unittest.mock import MagicMock

        assert isinstance(mock_run, MagicMock)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "vox"
        assert cmd[1] == "unmute"
        assert "--voice" in cmd
        assert "matilda" in cmd

    @patch("punt_vox.hooks.subprocess.run")
    def test_idle_prompt_calls_vox(self, mock_run: object) -> None:
        config = _make_config(speak="y")
        handle_notification({"notification_type": "idle_prompt"}, config)
        from unittest.mock import MagicMock

        assert isinstance(mock_run, MagicMock)
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# handle_post_bash tests
# ---------------------------------------------------------------------------


class TestHandlePostBash:
    def test_appends_signal(self, tmp_path: Path) -> None:
        from punt_vox.hooks import handle_post_bash

        config_path = tmp_path / ".vox" / "config.md"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('---\nnotify: "y"\n---\n')

        data: dict[str, object] = {
            "tool_response": {"exit_code": 0, "stdout": "5 passed in 1.2s"}
        }
        handle_post_bash(data, config_path)

        text = config_path.read_text()
        assert "vibe_signals" in text
        assert "tests-pass" in text

    def test_no_signal_no_write(self, tmp_path: Path) -> None:
        from punt_vox.hooks import handle_post_bash

        config_path = tmp_path / ".vox" / "config.md"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('---\nnotify: "y"\n---\n')

        data: dict[str, object] = {
            "tool_response": {"exit_code": 0, "stdout": "hello world"}
        }
        handle_post_bash(data, config_path)

        text = config_path.read_text()
        assert "vibe_signals" not in text

    def test_accumulates_signals(self, tmp_path: Path) -> None:
        from punt_vox.hooks import handle_post_bash

        config_path = tmp_path / ".vox" / "config.md"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            '---\nnotify: "y"\nvibe_signals: "lint-pass@11:00"\n---\n'
        )

        data: dict[str, object] = {
            "tool_response": {"exit_code": 0, "stdout": "5 passed in 1.2s"}
        }
        handle_post_bash(data, config_path)

        text = config_path.read_text()
        assert "lint-pass@11:00" in text
        assert "tests-pass" in text

    def test_invalid_tool_response(self, tmp_path: Path) -> None:
        from punt_vox.hooks import handle_post_bash

        config_path = tmp_path / ".vox" / "config.md"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('---\nnotify: "y"\n---\n')

        data: dict[str, object] = {"tool_response": "not a dict"}
        handle_post_bash(data, config_path)

        text = config_path.read_text()
        assert "vibe_signals" not in text


# ---------------------------------------------------------------------------
# handle_pre_compact tests
# ---------------------------------------------------------------------------


class TestHandlePreCompact:
    @patch("punt_vox.hooks._enqueue_audio")
    @patch("punt_vox.hooks.subprocess.run")
    def test_skip_when_notify_n(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        """PreCompact does nothing when notify=n."""
        config = _make_config(notify="n")
        handle_pre_compact(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._enqueue_audio")
    @patch("punt_vox.hooks.subprocess.run")
    def test_skip_when_notify_y(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        """PreCompact does nothing when notify=y (on-demand only)."""
        config = _make_config(notify="y")
        handle_pre_compact(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._enqueue_audio")
    def test_chime_mode_plays_chime(self, mock_enqueue: MagicMock) -> None:
        """PreCompact plays chime when notify=c and speak=n."""
        config = _make_config(notify="c", speak="n")
        handle_pre_compact(config)
        mock_enqueue.assert_called_once()

    @patch("punt_vox.hooks.subprocess.run")
    def test_voice_mode_speaks(self, mock_run: MagicMock) -> None:
        """PreCompact speaks a phrase when notify=c and speak=y."""
        config = _make_config(notify="c", speak="y", voice="matilda")
        handle_pre_compact(config)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "vox"
        assert cmd[1] == "unmute"
        assert "--voice" in cmd
        assert "matilda" in cmd
        # Phrase should be from the pool
        spoken_text = cmd[2]
        assert spoken_text in PRE_COMPACT_PHRASES

    @patch("punt_vox.hooks.subprocess.run")
    def test_voice_mode_no_voice_config(self, mock_run: MagicMock) -> None:
        """PreCompact works without explicit voice config."""
        config = _make_config(notify="c", speak="y")
        handle_pre_compact(config)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--voice" not in cmd
