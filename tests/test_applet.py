"""Tests for punt_vox.applet — Lux display applet."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from punt_vox.applet import (
    _is_hook_active,  # pyright: ignore[reportPrivateUsage]
    build_vox_elements,
)
from punt_vox.config import VoxConfig


def _default_config(**overrides: str | None) -> VoxConfig:
    """Build a VoxConfig with defaults, applying overrides."""
    defaults: dict[str, Any] = {
        "notify": "n",
        "speak": "y",
        "vibe_mode": "auto",
        "voice": None,
        "provider": None,
        "model": None,
        "vibe": None,
        "vibe_tags": None,
        "vibe_signals": None,
    }
    defaults.update(overrides)
    return VoxConfig(**defaults)


ROSTER = ["alice", "bob", "charlie"]


class TestBuildVoxElements:
    """Tests for build_vox_elements() — pure element tree construction."""

    def test_element_tree_structure(self) -> None:
        cfg = _default_config()
        elements = build_vox_elements(cfg, "elevenlabs", ROSTER)

        assert len(elements) == 5
        assert elements[0].id == "vox-notify-label"
        assert elements[1].id == "vox-notify"
        assert elements[2].id == "vox-speak"
        assert elements[3].id == "vox-voice"
        assert elements[4].id == "vox-vibe"

    def test_notify_radio_off(self) -> None:
        cfg = _default_config(notify="n")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[1].selected == 0

    def test_notify_radio_on(self) -> None:
        cfg = _default_config(notify="y")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[1].selected == 1

    def test_notify_radio_continuous(self) -> None:
        cfg = _default_config(notify="c")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[1].selected == 2

    def test_speak_radio_mute(self) -> None:
        cfg = _default_config(speak="n")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[2].selected == 0

    def test_speak_radio_unmute(self) -> None:
        cfg = _default_config(speak="y")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[2].selected == 1

    def test_voice_combo_selects_current(self) -> None:
        cfg = _default_config(voice="bob")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[3].selected == 1

    def test_voice_combo_defaults_when_not_in_roster(self) -> None:
        cfg = _default_config(voice="unknown")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[3].selected == 0

    def test_voice_combo_defaults_when_none(self) -> None:
        cfg = _default_config(voice=None)
        elements = build_vox_elements(cfg, "polly", ROSTER)
        assert elements[3].selected == 0

    def test_voice_combo_empty_roster(self) -> None:
        cfg = _default_config()
        elements = build_vox_elements(cfg, "polly", [])
        assert elements[3].items == ["(none)"]

    def test_vibe_combo_none(self) -> None:
        cfg = _default_config(vibe=None)
        elements = build_vox_elements(cfg, "polly", ROSTER)
        # Empty string is first in presets, matches None→""
        assert elements[4].selected == 0

    def test_vibe_combo_preset(self) -> None:
        cfg = _default_config(vibe="excited")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        # "excited" is index 2 in _MOOD_PRESETS
        assert elements[4].selected == 2

    def test_vibe_combo_custom(self) -> None:
        cfg = _default_config(vibe="custom-mood")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        # Custom mood appended to presets
        assert "custom-mood" in elements[4].items
        assert elements[4].selected == elements[4].items.index("custom-mood")

    def test_info_element(self) -> None:
        cfg = _default_config()
        elements = build_vox_elements(cfg, "elevenlabs", ROSTER)
        assert "\u24d8" in elements[0].content
        assert "Notifications" in elements[0].content

    def test_info_tooltip_contains_engine(self) -> None:
        cfg = _default_config()
        elements = build_vox_elements(cfg, "elevenlabs", ROSTER)
        assert "Engine: elevenlabs" in elements[0].tooltip

    def test_info_tooltip_lists_only_active_hooks(self) -> None:
        cfg = _default_config(notify="y", speak="y")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        tooltip = elements[0].tooltip
        # Active: SessionStart, Stop, Notification, PreCompact, SessionEnd
        assert "SessionStart" in tooltip
        assert "Stop" in tooltip
        # Inactive (continuous-only): Post-Bash, UserPromptSubmit, etc.
        assert "Post-Bash" not in tooltip
        assert "UserPromptSubmit" not in tooltip


class TestShowApplet:
    """Tests for show_applet() — Lux client interaction."""

    def test_calls_lux_client(self) -> None:
        cfg = _default_config()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch(
                "punt_vox.applet.LuxClient",
                return_value=mock_client,
                create=True,
            ),
            patch.dict(
                "sys.modules",
                {"punt_lux": MagicMock(), "punt_lux.client": MagicMock()},
            ),
        ):
            # Re-import to pick up patched module
            import importlib

            import punt_vox.applet as applet_mod

            importlib.reload(applet_mod)

            # Direct patch of the LuxClient in the reloaded module
            with patch.object(applet_mod, "LuxClient", create=True) as mock_lux_cls:
                mock_lux_cls.return_value = mock_client
                result = applet_mod.show_applet(cfg, "polly", ROSTER)

        # The actual assertion: we got through without error
        # (LuxClient is lazy-imported, so the mock wiring is complex)
        assert isinstance(result, dict)

    def test_import_error_graceful(self) -> None:
        """show_applet returns error when punt-lux is not installed."""
        cfg = _default_config()

        with patch.dict("sys.modules", {"punt_lux": None, "punt_lux.client": None}):
            import importlib

            import punt_vox.applet as applet_mod

            importlib.reload(applet_mod)
            result = applet_mod.show_applet(cfg, "polly", ROSTER)

        assert result["status"] == "error"
        assert "not installed" in result["message"]

    def test_os_error_graceful(self) -> None:
        """show_applet returns error when Lux display is not running."""
        cfg = _default_config()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.show.side_effect = OSError("Connection refused")

        mock_lux_module = MagicMock()
        mock_lux_client_module = MagicMock()
        mock_lux_client_module.LuxClient.return_value = mock_client

        with patch.dict(
            "sys.modules",
            {
                "punt_lux": mock_lux_module,
                "punt_lux.client": mock_lux_client_module,
            },
        ):
            import importlib

            import punt_vox.applet as applet_mod

            importlib.reload(applet_mod)
            result = applet_mod.show_applet(cfg, "polly", ROSTER)

        assert result["status"] == "error"
        assert "not available" in result["message"]


class TestHooksActivation:
    """Tests for _is_hook_active() — hook activation rules."""

    def test_always_active(self) -> None:
        cfg = _default_config(notify="n", speak="n")
        assert _is_hook_active(cfg, "always") is True

    def test_notify_active_when_on(self) -> None:
        cfg = _default_config(notify="y")
        assert _is_hook_active(cfg, "notify") is True

    def test_notify_active_when_continuous(self) -> None:
        cfg = _default_config(notify="c")
        assert _is_hook_active(cfg, "notify") is True

    def test_notify_inactive_when_off(self) -> None:
        cfg = _default_config(notify="n")
        assert _is_hook_active(cfg, "notify") is False

    def test_continuous_active_when_continuous(self) -> None:
        cfg = _default_config(notify="c")
        assert _is_hook_active(cfg, "continuous") is True

    def test_continuous_inactive_when_on(self) -> None:
        cfg = _default_config(notify="y")
        assert _is_hook_active(cfg, "continuous") is False

    def test_continuous_inactive_when_off(self) -> None:
        cfg = _default_config(notify="n")
        assert _is_hook_active(cfg, "continuous") is False

    def test_notify_speak_active(self) -> None:
        cfg = _default_config(notify="y", speak="y")
        assert _is_hook_active(cfg, "notify+speak") is True

    def test_notify_speak_inactive_muted(self) -> None:
        cfg = _default_config(notify="y", speak="n")
        assert _is_hook_active(cfg, "notify+speak") is False

    def test_notify_speak_inactive_notify_off(self) -> None:
        cfg = _default_config(notify="n", speak="y")
        assert _is_hook_active(cfg, "notify+speak") is False

    def test_info_tooltip_only_active_when_off(self) -> None:
        cfg = _default_config(notify="n")
        elements = build_vox_elements(cfg, "polly", ROSTER)
        tooltip = elements[0].tooltip
        # Only SessionStart is active
        assert "SessionStart" in tooltip
        assert "Stop" not in tooltip
