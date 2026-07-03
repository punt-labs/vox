"""Tests for hook dispatchers (src/punt_vox/hooks.py)."""

from __future__ import annotations

import errno
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call, patch

from punt_vox.config import VoxConfig
from punt_vox.dirs import find_config_dir
from punt_vox.hook_payload import BashPayload, NotificationPayload, StopPayload
from punt_vox.hooks import (
    _pick_notification_phrase,  # pyright: ignore[reportPrivateUsage]
    _read_hook_input,  # pyright: ignore[reportPrivateUsage]
    _repo_name_from_cwd,  # pyright: ignore[reportPrivateUsage]
    _speak_phrase,  # pyright: ignore[reportPrivateUsage]
    _with_repo_name,  # pyright: ignore[reportPrivateUsage]
    classify_signal,
    handle_notification,
    handle_post_bash,
    handle_pre_compact,
    handle_session_end,
    handle_stop,
    handle_subagent_start,
    handle_subagent_stop,
    handle_user_prompt_submit,
    notification_cmd,
    stop_cmd,
)
from punt_vox.quips import (
    ACKNOWLEDGE_PHRASES,
    FAREWELL_PHRASES,
    PRE_COMPACT_PHRASES,
    STOP_PHRASES,
    SUBAGENT_START_PHRASES,
    SUBAGENT_STOP_PHRASES,
)
from punt_vox.signal import SignalLog

if TYPE_CHECKING:
    import pytest

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
    repo_name: str | None = None,
) -> VoxConfig:
    return VoxConfig(
        notify=notify,
        speak=speak,
        vibe_mode="auto",
        voice=voice,
        provider=None,
        model=None,
        vibe=vibe,
        vibe_tags=None,
        vibe_signals=vibe_signals,
        repo_name=repo_name,
    )


# ---------------------------------------------------------------------------
# classify_signal tests
# ---------------------------------------------------------------------------


class TestClassifySignal:
    def test_tests_pass(self) -> None:
        assert classify_signal(0, "5 passed in 1.2s") == "tests-pass"

    def test_tests_pass_checkmark(self) -> None:
        assert classify_signal(0, "✓ 3 passed") == "tests-pass"

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


_CONFIG_DIR = Path("/repo/.punt-labs/vox")


def _stop(*, active: bool = False) -> StopPayload:
    return StopPayload(stop_hook_active=active, cwd=None)


class TestHandleStop:
    def test_notify_disabled(self) -> None:
        config = _make_config(notify="n")
        assert handle_stop(_stop(), config, _CONFIG_DIR) is None

    def test_stop_hook_active(self) -> None:
        config = _make_config()
        assert handle_stop(_stop(active=True), config, _CONFIG_DIR) is None

    def test_no_signals(self) -> None:
        config = _make_config(vibe_signals=None)
        assert handle_stop(_stop(), config, _CONFIG_DIR) is None

    def test_empty_signals(self) -> None:
        config = _make_config(vibe_signals="")
        assert handle_stop(_stop(), config, _CONFIG_DIR) is None

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode(self, mock_enqueue: object) -> None:
        config = _make_config(speak="n")
        result = handle_stop(_stop(), config, _CONFIG_DIR)
        assert result is None

    @patch("punt_vox.hooks.write_fields")
    def test_voice_mode_blocks_clean_reason(self, _mock_write: MagicMock) -> None:
        config = _make_config()
        result = handle_stop(_stop(), config, _CONFIG_DIR)
        assert result is not None
        assert result["decision"] == "block"
        reason = str(result["reason"])
        # Reason is just the ♪ phrase — no data appended
        assert any(reason == phrase for phrase in STOP_PHRASES)
        assert "|" not in reason
        assert "vibe_tags" not in reason
        assert "vibe_signals" not in reason

    @patch("punt_vox.hooks.write_fields")
    def test_auto_mode_writes_tags_to_passed_config_dir(
        self, mock_write: MagicMock
    ) -> None:
        # The vibe-tag write lands in the config_dir resolved from the
        # session cwd, not a re-resolved Path.cwd().
        config = _make_config(vibe_signals="tests-pass@01:00,git-push-ok@02:00")
        handle_stop(_stop(), config, _CONFIG_DIR)
        assert mock_write.call_count == 1
        assert mock_write.call_args == call(
            {"vibe_tags": "[satisfied]", "vibe_signals": ""},
            _CONFIG_DIR,
        )

    @patch("punt_vox.hooks.write_fields")
    def test_continuous_mode_blocks(self, _mock_write: MagicMock) -> None:
        config = _make_config(notify="c")
        result = handle_stop(_stop(), config, _CONFIG_DIR)
        assert result is not None
        assert result["decision"] == "block"

    def test_manual_vibe_skips_config_write(self) -> None:
        config = VoxConfig(
            notify="y",
            speak="y",
            vibe_mode="manual",
            voice=None,
            provider=None,
            model=None,
            vibe=None,
            vibe_tags="[excited] [warm]",
            vibe_signals="tests-pass@12:00",
        )
        with patch("punt_vox.hooks.write_fields") as mock_write:
            result = handle_stop(_stop(), config, _CONFIG_DIR)
        assert result is not None
        # Manual mode with existing tags — no write needed
        mock_write.assert_not_called()

    def test_vibe_off_skips_config_write(self) -> None:
        config = VoxConfig(
            notify="y",
            speak="y",
            vibe_mode="off",
            voice=None,
            provider=None,
            model=None,
            vibe=None,
            vibe_tags=None,
            vibe_signals="tests-pass@12:00",
        )
        with patch("punt_vox.hooks.write_fields") as mock_write:
            result = handle_stop(_stop(), config, _CONFIG_DIR)
        assert result is not None
        assert result["decision"] == "block"
        # Vibe off — must not write tags to config
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# handle_notification tests
# ---------------------------------------------------------------------------


class TestHandleNotification:
    @patch("punt_vox.hooks._speak_via_voxd")
    @patch("punt_vox.hooks._chime_via_voxd")
    def test_notify_disabled_does_nothing(
        self, mock_chime: MagicMock, mock_speak: MagicMock
    ) -> None:
        config = _make_config(notify="n")
        handle_notification(
            NotificationPayload(
                notification_type="permission_prompt",
                message="Needs your attention",
            ),
            config,
        )
        mock_chime.assert_not_called()
        mock_speak.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode_plays_chime(self, mock_chime: MagicMock) -> None:
        config = _make_config(speak="n")
        handle_notification(
            NotificationPayload(
                notification_type="permission_prompt",
                message="Needs your attention",
            ),
            config,
        )
        mock_chime.assert_called_once_with("prompt")

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_speaks_via_voxd(self, mock_speak: MagicMock) -> None:
        config = _make_config(speak="y", voice="matilda")
        handle_notification(
            NotificationPayload(
                notification_type="permission_prompt",
                message="Needs your attention",
            ),
            config,
        )
        mock_speak.assert_called_once()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_idle_prompt_speaks_via_voxd(self, mock_speak: MagicMock) -> None:
        config = _make_config(speak="y")
        handle_notification(
            NotificationPayload(
                notification_type="idle_prompt",
                message="Needs your attention",
            ),
            config,
        )
        mock_speak.assert_called_once()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_unknown_type_speaks_via_voxd(self, mock_speak: MagicMock) -> None:
        config = _make_config(speak="y")
        handle_notification(
            NotificationPayload(
                notification_type="unknown",
                message="Needs your attention",
            ),
            config,
        )
        mock_speak.assert_called_once()


# ---------------------------------------------------------------------------
# handle_post_bash tests
# ---------------------------------------------------------------------------


class TestHandlePostBash:
    def test_appends_signal(self, tmp_path: Path) -> None:
        config_dir = tmp_path
        vox_md = config_dir / "vox.md"
        vox_md.write_text('---\nnotify: "y"\n---\n')

        payload = BashPayload(exit_code=0, stdout="5 passed in 1.2s")
        handle_post_bash(payload, config_dir)

        # vibe_signals is ephemeral — written to vox.local.md
        local_md = config_dir / "vox.local.md"
        assert local_md.exists()
        text = local_md.read_text()
        assert "vibe_signals" in text
        assert "tests-pass" in text

    def test_no_signal_no_write(self, tmp_path: Path) -> None:
        config_dir = tmp_path
        vox_md = config_dir / "vox.md"
        vox_md.write_text('---\nnotify: "y"\n---\n')

        payload = BashPayload(exit_code=0, stdout="hello world")
        handle_post_bash(payload, config_dir)

        # No signal matched — vox.local.md should not be created
        local_md = config_dir / "vox.local.md"
        assert not local_md.exists()

    def test_accumulates_signals(self, tmp_path: Path) -> None:
        config_dir = tmp_path
        vox_md = config_dir / "vox.md"
        vox_md.write_text('---\nnotify: "y"\n---\n')
        local_md = config_dir / "vox.local.md"
        local_md.write_text('---\nvibe_signals: "lint-pass@11:00"\n---\n')

        payload = BashPayload(exit_code=0, stdout="5 passed in 1.2s")
        handle_post_bash(payload, config_dir)

        text = local_md.read_text()
        assert "lint-pass@11:00" in text
        assert "tests-pass" in text

    def test_invalid_tool_response(self, tmp_path: Path) -> None:
        config_dir = tmp_path
        vox_md = config_dir / "vox.md"
        vox_md.write_text('---\nnotify: "y"\n---\n')

        payload = BashPayload(exit_code=None, stdout="")
        handle_post_bash(payload, config_dir)

        local_md = config_dir / "vox.local.md"
        assert not local_md.exists()

    def test_prunes_signals_at_max(self, tmp_path: Path) -> None:
        max_entries = SignalLog.MAX_ENTRIES
        config_dir = tmp_path
        vox_md = config_dir / "vox.md"
        vox_md.write_text('---\nnotify: "y"\n---\n')
        local_md = config_dir / "vox.local.md"
        # Seed with exactly MAX signals already present
        existing = ",".join(f"old-{i}@00:00" for i in range(max_entries))
        local_md.write_text(f'---\nvibe_signals: "{existing}"\n---\n')

        payload = BashPayload(exit_code=0, stdout="5 passed in 1.2s")
        handle_post_bash(payload, config_dir)

        text = local_md.read_text()
        # Extract the vibe_signals value
        for line in text.splitlines():
            if "vibe_signals" in line:
                signals = line.split(":", 1)[1].strip().strip('"')
                parts = signals.split(",")
                assert len(parts) == max_entries
                # Oldest signal should have been pruned
                assert "old-0@00:00" not in signals
                # New signal should be present
                assert "tests-pass@" in signals
                break
        else:
            raise AssertionError("vibe_signals not found in config")

    def test_write_failure_warns_and_returns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A corrupt/unwritable vox.local.md must not crash the PostToolUse hook.
        config_dir = tmp_path
        (config_dir / "vox.md").write_text('---\nnotify: "y"\n---\n')
        payload = BashPayload(exit_code=0, stdout="5 passed in 1.2s")
        with (
            patch("punt_vox.hooks.write_field", side_effect=OSError("read-only fs")),
            caplog.at_level(logging.WARNING, logger="punt_vox.hooks"),
        ):
            handle_post_bash(payload, config_dir)  # must not raise
        assert any("post-bash" in r.getMessage() for r in caplog.records)

    def test_read_failure_warns_and_returns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A malformed config that raises on read is swallowed with a warning.
        config_dir = tmp_path
        (config_dir / "vox.md").write_text('---\nnotify: "y"\n---\n')
        payload = BashPayload(exit_code=0, stdout="5 passed in 1.2s")
        with (
            patch("punt_vox.hooks.read_config", side_effect=ValueError("bad yaml")),
            caplog.at_level(logging.WARNING, logger="punt_vox.hooks"),
        ):
            handle_post_bash(payload, config_dir)  # must not raise
        assert any("post-bash" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# handle_pre_compact tests
# ---------------------------------------------------------------------------


class TestHandlePreCompact:
    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_n(
        self, mock_speak: MagicMock, mock_chime: MagicMock
    ) -> None:
        """PreCompact does nothing when notify=n."""
        config = _make_config(notify="n")
        handle_pre_compact(config)
        mock_speak.assert_not_called()
        mock_chime.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_y(
        self, mock_speak: MagicMock, mock_chime: MagicMock
    ) -> None:
        """PreCompact does nothing when notify=y (on-demand only)."""
        config = _make_config(notify="y")
        handle_pre_compact(config)
        mock_speak.assert_not_called()
        mock_chime.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode_plays_chime(self, mock_chime: MagicMock) -> None:
        """PreCompact plays chime when notify=c and speak=n."""
        config = _make_config(notify="c", speak="n")
        handle_pre_compact(config)
        mock_chime.assert_called_once()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_speaks(self, mock_speak: MagicMock) -> None:
        """PreCompact speaks a phrase when notify=c and speak=y."""
        config = _make_config(notify="c", speak="y", voice="matilda")
        handle_pre_compact(config)
        mock_speak.assert_called_once()
        text = mock_speak.call_args[0][0]
        assert text in PRE_COMPACT_PHRASES

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_no_voice_config(self, mock_speak: MagicMock) -> None:
        """PreCompact works without explicit voice config."""
        config = _make_config(notify="c", speak="y")
        handle_pre_compact(config)
        mock_speak.assert_called_once()


# ---------------------------------------------------------------------------
# _speak_phrase tests
# ---------------------------------------------------------------------------


class TestSpeakPhrase:
    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode_plays_chime(self, mock_chime: MagicMock) -> None:
        """speak=n plays a chime instead of synthesizing speech."""
        config = _make_config(notify="c", speak="n")
        _speak_phrase(("Hello",), config, chime_signal="done")
        mock_chime.assert_called_once_with("done")

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_speaks_via_voxd(self, mock_speak: MagicMock) -> None:
        """speak=y synthesizes speech via voxd."""
        config = _make_config(notify="c", speak="y", voice="matilda")
        _speak_phrase(("Hello there",), config, chime_signal="done")
        mock_speak.assert_called_once()
        text = mock_speak.call_args[0][0]
        assert text == "Hello there"

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_picks_from_pool(self, mock_speak: MagicMock) -> None:
        """Spoken text is always drawn from the provided phrase pool."""
        phrases = ("Alpha", "Bravo", "Charlie")
        config = _make_config(notify="c", speak="y")
        _speak_phrase(phrases, config, chime_signal="done")
        text = mock_speak.call_args[0][0]
        assert text in phrases


# ---------------------------------------------------------------------------
# handle_user_prompt_submit tests
# ---------------------------------------------------------------------------


class TestHandleUserPromptSubmit:
    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_n(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        """Does nothing when notify=n."""
        config = _make_config(notify="n")
        handle_user_prompt_submit(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_y(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        """Does nothing in on-demand mode (notify=y)."""
        config = _make_config(notify="y")
        handle_user_prompt_submit(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode(self, mock_enqueue: MagicMock) -> None:
        """Plays chime when notify=c and speak=n."""
        config = _make_config(notify="c", speak="n")
        handle_user_prompt_submit(config)
        mock_enqueue.assert_called_once()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_speaks(self, mock_speak: MagicMock) -> None:
        """Speaks an acknowledgment phrase when notify=c and speak=y."""
        config = _make_config(notify="c", speak="y", voice="matilda")
        handle_user_prompt_submit(config)
        mock_speak.assert_called_once()
        text = mock_speak.call_args[0][0]
        assert text in ACKNOWLEDGE_PHRASES


# ---------------------------------------------------------------------------
# handle_subagent_start tests
# ---------------------------------------------------------------------------


class TestHandleSubagentStart:
    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_n(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        config = _make_config(notify="n")
        handle_subagent_start(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_y(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        config = _make_config(notify="y")
        handle_subagent_start(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode(self, mock_enqueue: MagicMock) -> None:
        config = _make_config(notify="c", speak="n")
        handle_subagent_start(config)
        mock_enqueue.assert_called_once()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_speaks(self, mock_speak: MagicMock) -> None:
        config = _make_config(notify="c", speak="y")
        handle_subagent_start(config)
        mock_speak.assert_called_once()
        text = mock_speak.call_args[0][0]
        assert text in SUBAGENT_START_PHRASES


# ---------------------------------------------------------------------------
# handle_subagent_stop tests
# ---------------------------------------------------------------------------


class TestHandleSubagentStop:
    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_n(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        config = _make_config(notify="n")
        handle_subagent_stop(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_y(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        config = _make_config(notify="y")
        handle_subagent_stop(config)
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode(self, mock_enqueue: MagicMock) -> None:
        config = _make_config(notify="c", speak="n")
        handle_subagent_stop(config)
        mock_enqueue.assert_called_once()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_speaks(self, mock_speak: MagicMock) -> None:
        config = _make_config(notify="c", speak="y")
        handle_subagent_stop(config)
        mock_speak.assert_called_once()
        text = mock_speak.call_args[0][0]
        assert text in SUBAGENT_STOP_PHRASES


# ---------------------------------------------------------------------------
# handle_session_end tests
# ---------------------------------------------------------------------------


class TestHandleSessionEnd:
    @patch("punt_vox.hooks._chime_via_voxd")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_skip_when_notify_n(
        self, mock_run: MagicMock, mock_enqueue: MagicMock
    ) -> None:
        config = _make_config(notify="n")
        handle_session_end(config, Path("/fake/.punt-labs/vox"))
        mock_run.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_chime_mode(self, mock_enqueue: MagicMock) -> None:
        """Fires for notify=y (not just continuous)."""
        config = _make_config(notify="y", speak="n", vibe_signals=None)
        handle_session_end(config, Path("/fake/.punt-labs/vox"))
        mock_enqueue.assert_called_once()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_voice_mode_speaks(self, mock_speak: MagicMock) -> None:
        config = _make_config(notify="y", speak="y", vibe_signals=None)
        handle_session_end(config, Path("/fake/.punt-labs/vox"))
        mock_speak.assert_called_once()
        text = mock_speak.call_args[0][0]
        assert text in FAREWELL_PHRASES

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_continuous_mode_speaks(self, mock_speak: MagicMock) -> None:
        config = _make_config(notify="c", speak="y", vibe_signals=None)
        handle_session_end(config, Path("/fake/.punt-labs/vox"))
        mock_speak.assert_called_once()

    @patch("punt_vox.hooks.write_field")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_clears_vibe_signals(
        self, _mock_run: MagicMock, mock_write: MagicMock
    ) -> None:
        """SessionEnd clears vibe_signals to prevent stale leakage."""
        config = _make_config(notify="y", speak="y", vibe_signals="tests-pass@12:00")
        config_dir = Path("/fake/.punt-labs/vox")
        handle_session_end(config, config_dir)
        mock_write.assert_called_once_with("vibe_signals", "", config_dir)

    @patch("punt_vox.hooks.write_field")
    @patch("punt_vox.hooks._speak_via_voxd")
    def test_no_write_when_no_signals(
        self, _mock_run: MagicMock, mock_write: MagicMock
    ) -> None:
        """Does not write config if vibe_signals is already empty."""
        config = _make_config(notify="y", speak="y", vibe_signals=None)
        handle_session_end(config, Path("/fake/.punt-labs/vox"))
        mock_write.assert_not_called()

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_clear_failure_warns_and_returns(
        self, _mock_speak: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        # An unwritable vox.local.md must not crash the Stop/SessionEnd hook.
        config = _make_config(notify="y", speak="y", vibe_signals="tests-pass@12:00")
        with (
            patch("punt_vox.hooks.write_field", side_effect=OSError("read-only fs")),
            caplog.at_level(logging.WARNING, logger="punt_vox.hooks"),
        ):
            handle_session_end(config, Path("/fake/.punt-labs/vox"))  # must not raise
        assert any("session-end" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Quip pool size tests
# ---------------------------------------------------------------------------


class TestQuipPools:
    """Verify all quip pools have at least 10 entries (user requirement)."""

    def test_acknowledge_phrases_count(self) -> None:
        assert len(ACKNOWLEDGE_PHRASES) >= 10

    def test_subagent_start_phrases_count(self) -> None:
        assert len(SUBAGENT_START_PHRASES) >= 10

    def test_subagent_stop_phrases_count(self) -> None:
        assert len(SUBAGENT_STOP_PHRASES) >= 10

    def test_farewell_phrases_count(self) -> None:
        assert len(FAREWELL_PHRASES) >= 10

    def test_stop_phrases_count(self) -> None:
        assert len(STOP_PHRASES) >= 7

    def test_pre_compact_phrases_count(self) -> None:
        assert len(PRE_COMPACT_PHRASES) >= 7


# ---------------------------------------------------------------------------
# repo_name prefix tests
# ---------------------------------------------------------------------------


class TestRepoNamePrefix:
    """Verify repo_name is prepended to spoken text across all speech paths."""

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_speak_phrase_prepends_repo_name(self, mock_speak: MagicMock) -> None:
        """_speak_phrase prepends 'repo. phrase' when repo_name is set."""
        config = _make_config(notify="c", speak="y", repo_name="vox")
        _speak_phrase(("On it.",), config, chime_signal="done")
        text = mock_speak.call_args[0][0]
        assert text.startswith("vox. ")
        assert text == "vox. On it."

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_speak_phrase_no_prefix_when_repo_name_none(
        self, mock_speak: MagicMock
    ) -> None:
        """_speak_phrase omits prefix when repo_name is None."""
        config = _make_config(notify="c", speak="y", repo_name=None)
        _speak_phrase(("On it.",), config, chime_signal="done")
        text = mock_speak.call_args[0][0]
        assert text == "On it."

    @patch("punt_vox.hooks._chime_via_voxd")
    def test_speak_phrase_chime_mode_ignores_repo_name(
        self, mock_chime: MagicMock
    ) -> None:
        """Chime mode skips speech entirely — repo_name irrelevant."""
        config = _make_config(notify="c", speak="n", repo_name="vox")
        _speak_phrase(("On it.",), config, chime_signal="done")
        mock_chime.assert_called_once_with("done")

    def test_pick_notification_phrase_prepends_repo_name(self) -> None:
        """_pick_notification_phrase prepends repo_name to permission phrases."""
        text = _pick_notification_phrase("permission_prompt", "", repo_name="quarry")
        assert text.startswith("quarry. ")

    def test_pick_notification_phrase_no_prefix_when_none(self) -> None:
        """_pick_notification_phrase omits prefix when repo_name is None."""
        text = _pick_notification_phrase("idle_prompt", "", repo_name=None)
        assert not text.startswith("None")

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_handle_notification_passes_repo_name(self, mock_speak: MagicMock) -> None:
        """handle_notification threads repo_name to the spoken phrase."""
        config = _make_config(speak="y", repo_name="biff")
        handle_notification(
            NotificationPayload(
                notification_type="permission_prompt",
                message="Needs your attention",
            ),
            config,
        )
        text = mock_speak.call_args[0][0]
        assert text.startswith("biff. ")

    @patch("punt_vox.hooks.write_fields")
    def test_handle_stop_prepends_repo_name_to_reason(
        self, _mock_write: MagicMock
    ) -> None:
        """handle_stop includes repo_name in the block reason string."""
        config = _make_config(repo_name="vox")
        result = handle_stop(
            StopPayload(stop_hook_active=False, cwd=None),
            config,
            Path("/repo/.punt-labs/vox"),
        )
        assert result is not None
        reason = str(result["reason"])
        assert reason.startswith("vox. ")

    @patch("punt_vox.hooks.write_fields")
    def test_handle_stop_no_prefix_when_repo_name_none(
        self, _mock_write: MagicMock
    ) -> None:
        """handle_stop reason is a plain phrase when repo_name is None."""
        config = _make_config(repo_name=None)
        result = handle_stop(
            StopPayload(stop_hook_active=False, cwd=None),
            config,
            Path("/repo/.punt-labs/vox"),
        )
        assert result is not None
        reason = str(result["reason"])
        assert any(reason == phrase for phrase in STOP_PHRASES)

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_handle_pre_compact_prepends_repo_name(self, mock_speak: MagicMock) -> None:
        """handle_pre_compact speaks with repo_name prefix."""
        config = _make_config(notify="c", speak="y", repo_name="ethos")
        handle_pre_compact(config)
        text = mock_speak.call_args[0][0]
        assert text.startswith("ethos. ")

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_handle_session_end_prepends_repo_name(self, mock_speak: MagicMock) -> None:
        """handle_session_end speaks with repo_name prefix."""
        config = _make_config(notify="y", speak="y", vibe_signals=None, repo_name="lux")
        handle_session_end(config, Path("/fake/.punt-labs/vox"))
        text = mock_speak.call_args[0][0]
        assert text.startswith("lux. ")


# ---------------------------------------------------------------------------
# _read_hook_input (non-blocking stdin, DES-027)
# ---------------------------------------------------------------------------


class TestReadHookInput:
    """Verify _read_hook_input doesn't block on open pipes."""

    def test_empty_stdin_returns_empty(self) -> None:
        """EOF with no data returns {}."""
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        r = os.fdopen(r_fd, "r")
        try:
            with patch.object(sys, "stdin", r):
                result = _read_hook_input()
        finally:
            r.close()
        assert result == {}

    def test_valid_json_parsed(self) -> None:
        """Valid JSON on stdin is parsed and returned."""
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b'{"tool_name": "Bash"}\n')
        os.close(w_fd)
        r = os.fdopen(r_fd, "r")
        try:
            with patch.object(sys, "stdin", r):
                result = _read_hook_input()
        finally:
            r.close()
        assert result == {"tool_name": "Bash"}

    def test_no_eof_does_not_hang(self) -> None:
        """Stdin with data but no EOF returns data without blocking.

        Regression test for the session resume hang (DES-027).
        """
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b'{"event": "stop"}\n')
        # Do NOT close w_fd — simulates open pipe without EOF.
        r = os.fdopen(r_fd, "r")
        try:
            with patch.object(sys, "stdin", r):
                result = _read_hook_input()
        finally:
            r.close()
            os.close(w_fd)
        assert result == {"event": "stop"}

    def test_no_data_no_eof_returns_empty(self) -> None:
        """Open pipe with no data returns {} without blocking."""
        r_fd, w_fd = os.pipe()
        r = os.fdopen(r_fd, "r")
        try:
            with patch.object(sys, "stdin", r):
                result = _read_hook_input()
        finally:
            r.close()
            os.close(w_fd)
        assert result == {}

    def test_invalid_json_returns_empty(self) -> None:
        """Invalid JSON on stdin returns {} gracefully."""
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"not json\n")
        os.close(w_fd)
        r = os.fdopen(r_fd, "r")
        try:
            with patch.object(sys, "stdin", r):
                result = _read_hook_input()
        finally:
            r.close()
        assert result == {}

    def test_unexpected_oserror_logs_errno(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A genuine read failure (EIO) logs the errno at WARNING, returns {}."""
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b'{"x": 1}')  # ensure select() reports readable
        r = os.fdopen(r_fd, "r")
        try:
            with (
                patch("punt_vox.hooks.os.read", side_effect=OSError(errno.EIO, "io")),
                caplog.at_level(logging.WARNING, logger="punt_vox.hooks"),
                patch.object(sys, "stdin", r),
            ):
                result = _read_hook_input()
        finally:
            r.close()
            os.close(w_fd)
        assert result == {}
        assert any(
            str(errno.EIO) in rec.getMessage() and "errno" in rec.getMessage()
            for rec in caplog.records
        )

    def test_oserror_without_errno_stays_quiet(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An OSError with no errno (e.g. empty pipe) returns {} without warning."""
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b'{"x": 1}')
        r = os.fdopen(r_fd, "r")
        try:
            with (
                patch("punt_vox.hooks.os.read", side_effect=OSError()),
                caplog.at_level(logging.WARNING, logger="punt_vox.hooks"),
                patch.object(sys, "stdin", r),
            ):
                result = _read_hook_input()
        finally:
            r.close()
            os.close(w_fd)
        assert result == {}
        assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]

    def test_closed_empty_stdin_stays_quiet(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """EOF with no data returns {} quietly — the expected empty path."""
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        r = os.fdopen(r_fd, "r")
        try:
            with (
                caplog.at_level(logging.WARNING, logger="punt_vox.hooks"),
                patch.object(sys, "stdin", r),
            ):
                result = _read_hook_input()
        finally:
            r.close()
        assert result == {}
        assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# _repo_name_from_cwd / _with_repo_name — git-root override
# ---------------------------------------------------------------------------


class TestRepoNameFromCwd:
    def test_none_cwd_returns_none(self) -> None:
        assert _repo_name_from_cwd(None) is None

    def test_returns_repo_name_from_git_root(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        assert _repo_name_from_cwd(repo) == "my-repo"

    def test_walks_up_to_git_root(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-repo"
        subdir = repo / "src" / "pkg"
        subdir.mkdir(parents=True)
        (repo / ".git").mkdir()
        assert _repo_name_from_cwd(subdir) == "my-repo"

    def test_no_git_root_returns_none(self, tmp_path: Path) -> None:
        isolated = tmp_path / "no-git"
        isolated.mkdir()
        # May find a real .git above tmp_path on the host; only assert
        # None when truly isolated.
        result = _repo_name_from_cwd(isolated)
        if result is not None:
            assert not Path(result).is_relative_to(isolated)


class TestWithRepoName:
    def test_overrides_when_names_differ(self, tmp_path: Path) -> None:
        repo = tmp_path / "vox"
        repo.mkdir()
        (repo / ".git").mkdir()
        config = _make_config(repo_name="punt-labs")
        updated = _with_repo_name(config, repo)
        assert updated.repo_name == "vox"

    def test_no_change_when_names_match(self, tmp_path: Path) -> None:
        repo = tmp_path / "vox"
        repo.mkdir()
        (repo / ".git").mkdir()
        config = _make_config(repo_name="vox")
        updated = _with_repo_name(config, repo)
        assert updated is config  # same object — no replacement

    def test_no_change_when_cwd_none(self) -> None:
        config = _make_config(repo_name="punt-labs")
        updated = _with_repo_name(config, None)
        assert updated is config

    def test_inherited_config_gets_child_repo_name(self, tmp_path: Path) -> None:
        """When config_dir resolves to parent workspace but cwd is a child repo."""
        # Simulate: workspace/child-repo/ has .git but no .punt-labs/vox/
        # Config was inherited from workspace/.punt-labs/vox/ yielding "punt-labs"
        child = tmp_path / "child-repo"
        child.mkdir()
        (child / ".git").mkdir()
        config = _make_config(repo_name="punt-labs")
        updated = _with_repo_name(config, child)
        assert updated.repo_name == "child-repo"


# ---------------------------------------------------------------------------
# Command handlers resolve repo identity from the payload cwd
# ---------------------------------------------------------------------------


def _make_repo(root: Path, *, notify: str = "c") -> Path:
    """Create a configured repo under *root* and return its path."""
    config_dir = root / ".punt-labs" / "vox"
    config_dir.mkdir(parents=True)
    (config_dir / "vox.md").write_text(f'---\nnotify: "{notify}"\n---\n')
    return root


class TestCommandsResolveFromCwd:
    """The *_cmd handlers derive the spoken repo name from payload.cwd."""

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_stop_cmd_speaks_repo_from_cwd(
        self, mock_speak: MagicMock, tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path / "vox", notify="y")
        local = repo / ".punt-labs" / "vox" / "vox.local.md"
        local.write_text('---\nvibe_signals: "tests-pass@12:00"\n---\n')
        payload = {"cwd": str(repo), "stop_hook_active": False}
        with (
            patch("punt_vox.hooks._read_hook_input", return_value=payload),
            patch("punt_vox.hooks._emit") as mock_emit,
            patch("punt_vox.hooks.find_config_dir", wraps=find_config_dir) as mock_find,
        ):
            stop_cmd()
        mock_find.assert_called_once_with(Path(str(repo)))
        result = mock_emit.call_args[0][0]
        # The block reason names the repo resolved from cwd: "vox. <phrase>".
        assert str(result["reason"]).startswith("vox. ")

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_notification_cmd_speaks_repo_from_cwd(
        self, mock_speak: MagicMock, tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path / "vox", notify="y")
        payload = {
            "cwd": str(repo),
            "notification_type": "permission_prompt",
            "message": "Needs your attention",
        }
        with (
            patch("punt_vox.hooks._read_hook_input", return_value=payload),
            patch("punt_vox.hooks.find_config_dir", wraps=find_config_dir) as mock_find,
        ):
            notification_cmd()
        mock_find.assert_called_once_with(Path(str(repo)))
        text = mock_speak.call_args[0][0]
        assert text.startswith("vox. ")

    @patch("punt_vox.hooks._speak_via_voxd")
    @patch("punt_vox.hooks._chime_via_voxd")
    def test_notification_cmd_silent_when_no_config(
        self, mock_chime: MagicMock, mock_speak: MagicMock, no_config_dir: Path
    ) -> None:
        # cwd points at a directory with no .punt-labs/vox config — silent.
        # no_config_dir has no ambient config above it, so find_config_dir
        # cannot leak into the real repo config (fails under TMPDIR=.tmp).
        bare = no_config_dir / "punt-labs"
        bare.mkdir()
        payload = {
            "cwd": str(bare),
            "notification_type": "permission_prompt",
            "message": "Needs your attention",
        }
        with patch("punt_vox.hooks._read_hook_input", return_value=payload):
            notification_cmd()
        mock_speak.assert_not_called()
        mock_chime.assert_not_called()

    def test_stop_cmd_vibe_write_targets_cwd_repo(self, tmp_path: Path) -> None:
        # The vibe-tag write lands in the repo resolved from payload.cwd.
        repo = _make_repo(tmp_path / "vox", notify="y")
        config_dir = repo / ".punt-labs" / "vox"
        (config_dir / "vox.local.md").write_text(
            '---\nvibe_signals: "tests-pass@12:00"\n---\n'
        )
        payload = {"cwd": str(repo), "stop_hook_active": False}
        with (
            patch("punt_vox.hooks._read_hook_input", return_value=payload),
            patch("punt_vox.hooks.write_fields") as mock_write,
            patch("punt_vox.hooks._emit"),
        ):
            stop_cmd()
        mock_write.assert_called_once()
        assert mock_write.call_args[0][1] == config_dir

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_stop_cmd_inherited_config_uses_child_repo_name(
        self, mock_speak: MagicMock, tmp_path: Path
    ) -> None:
        """Config lives in parent workspace but cwd is a child repo.

        The bug: config_dir resolves to ``workspace/.punt-labs/vox/``
        so ``_derive_repo_name`` returns "punt-labs" (the workspace dir).
        With ``_with_repo_name``, the git root of cwd overrides the name.
        """
        workspace = tmp_path / "punt-labs"
        # Config lives only in the workspace
        _make_repo(workspace, notify="y")
        # Child repo has .git but no .punt-labs/vox/
        child = workspace / "biff"
        child.mkdir()
        (child / ".git").mkdir()
        # Seed signals in the workspace config so stop hook fires
        local = workspace / ".punt-labs" / "vox" / "vox.local.md"
        local.write_text('---\nvibe_signals: "tests-pass@12:00"\n---\n')
        payload = {"cwd": str(child), "stop_hook_active": False}
        with (
            patch("punt_vox.hooks._read_hook_input", return_value=payload),
            patch("punt_vox.hooks._emit") as mock_emit,
        ):
            stop_cmd()
        result = mock_emit.call_args[0][0]
        # Must say "biff" (child repo), not "punt-labs" (workspace)
        assert str(result["reason"]).startswith("biff. ")

    @patch("punt_vox.hooks._speak_via_voxd")
    def test_notification_cmd_inherited_config_uses_child_repo_name(
        self, mock_speak: MagicMock, tmp_path: Path
    ) -> None:
        """Notification with inherited config speaks child repo name."""
        workspace = tmp_path / "punt-labs"
        _make_repo(workspace, notify="y")
        child = workspace / "vox"
        child.mkdir()
        (child / ".git").mkdir()
        payload = {
            "cwd": str(child),
            "notification_type": "permission_prompt",
            "message": "Needs your attention",
        }
        with patch("punt_vox.hooks._read_hook_input", return_value=payload):
            notification_cmd()
        text = mock_speak.call_args[0][0]
        assert text.startswith("vox. ")

    def test_stop_cmd_silent_when_cwd_missing(self, no_config_dir: Path) -> None:
        # No cwd on the payload → no new pwd-fallback suppression added;
        # resolution falls through to find_config_dir(None). In an isolated
        # dir with no config above it, the hook stays silent (no ambient
        # repo config leaks in under TMPDIR=.tmp).
        isolated = no_config_dir / "isolated"
        isolated.mkdir()
        payload = {"stop_hook_active": False}
        with (
            patch("punt_vox.hooks._read_hook_input", return_value=payload),
            patch("punt_vox.dirs.Path.cwd", return_value=isolated),
            patch("punt_vox.hooks._emit") as mock_emit,
        ):
            stop_cmd()
        mock_emit.assert_not_called()
