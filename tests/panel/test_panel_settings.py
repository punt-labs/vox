"""Unit tests for the panel's shared settings and cascade commits."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final

import pytest

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.config import ConfigStore
from punt_vox.panel.control_push import ControlPush
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.panel_settings import PanelSettings
from punt_vox.panel.state import PanelState

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voice_target import VoiceTarget


@final
class _Roster:
    def voices(self, provider: VoiceTarget) -> list[str]:
        assert provider.provider == "openai"
        return (
            ["alloy", "marin", "nova"]
            if provider.model == "gpt-4o-mini-tts"
            else ["alloy", "nova"]
        )


class TestPanelSettings:
    def test_roster_failure_logs_under_settings_module(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _UnavailableRoster:
            def voices(self, provider: VoiceTarget) -> list[str]:
                raise VoxdConnectionError(f"no roster for {provider.provider}")

        store = ConfigStore(tmp_path)
        settings = PanelSettings(_UnavailableRoster(), store)

        with caplog.at_level(logging.WARNING, logger="punt_vox.panel.panel_settings"):
            assert settings.commit_provider("openai") is ControlPush.CORRECT

        assert any(
            record.name == "punt_vox.panel.panel_settings"
            and "roster fetch failed during provider-switch" in record.message
            for record in caplog.records
        )
        assert settings.scene().notice == PanelNotice.voxd_unavailable()
        assert store.read_field("provider") is None

    def test_provider_cascades_model_and_voice(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path)
        settings = PanelSettings(_Roster(), store)

        assert settings.commit_provider("openai") is ControlPush.REFRESH

        assert store.read_field("provider") == "openai"
        assert store.read_field("model") == "tts-1"
        assert store.read_field("voice") == "alloy"
        assert settings.snapshot().roster == ("alloy", "nova")
        assert settings.commit_provider("openai") is ControlPush.NONE

    def test_model_resets_voice_to_first_on_current_roster(
        self, tmp_path: Path
    ) -> None:
        store = ConfigStore(tmp_path)
        store.write_fields({"provider": "openai", "model": "tts-1", "voice": "nova"})
        settings = PanelSettings(_Roster(), store)
        settings.replace_if_current(
            settings.snapshot(),
            PanelState.empty().with_provider(
                "openai", roster=("alloy", "nova"), model="tts-1", voice="nova"
            ),
            PanelNotice.silent(),
        )

        assert settings.commit_model("gpt-4o-mini-tts") is ControlPush.REFRESH

        assert store.read_field("model") == "gpt-4o-mini-tts"
        assert store.read_field("voice") == "alloy"
        assert settings.snapshot().voice == "alloy"
        assert settings.snapshot().roster == ("alloy", "marin", "nova")

        assert settings.commit_model("tts-1") is ControlPush.REFRESH

        assert settings.snapshot().roster == ("alloy", "nova")

    def test_voice_pick_does_not_overwrite_a_newer_model(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path)
        settings = PanelSettings(_Roster(), store)
        settings.commit_provider("openai")
        settings.commit_model("gpt-4o-mini-tts")
        shown = settings.snapshot()

        settings.commit_model("tts-1")

        assert settings.commit_voice(shown, "marin") is ControlPush.CORRECT
        assert store.read().voice == "alloy"

    def test_provider_yields_when_another_thread_won_the_race(
        self, tmp_path: Path
    ) -> None:
        """A provider change during the roster fetch wins over this commit."""

        class _RacingRoster:
            def voices(self, provider: VoiceTarget) -> list[str]:
                assert provider.provider == "espeak"
                settings.replace_if_current(
                    settings.snapshot(),
                    settings.snapshot().with_provider(
                        "openai", roster=("alloy",), model="tts-1", voice="alloy"
                    ),
                    PanelNotice.silent(),
                )
                return ["benno"]

        store = ConfigStore(tmp_path)
        settings = PanelSettings(_RacingRoster(), store)
        settings.replace_if_current(
            settings.snapshot(),
            PanelState.empty().with_provider(
                "elevenlabs", roster=("benno",), model=None, voice="benno"
            ),
            PanelNotice.silent(),
        )

        changed = settings.commit_provider("espeak")

        assert changed is ControlPush.CORRECT
        assert store.read_field("provider") is None
        assert store.read_field("voice") is None
        assert settings.scene().provider == "openai"

    def test_model_click_with_no_provider_selected_corrects(
        self, tmp_path: Path
    ) -> None:
        """A model commit dispatched before any provider is chosen is abandoned.

        ``apply_event``'s click target already refuses a MODEL
        click with no provider selected (an empty model list makes
        ``ModelControl`` raise before ``commit_model`` runs), so this drives
        ``PanelSettings.commit_model`` directly to prove its own no-provider
        guard also answers :attr:`~ControlPush.CORRECT`: the widget already
        applied the pick optimistically and ``_state`` never moves, so only a
        full reinstall snaps it back.
        """
        store = ConfigStore(tmp_path)
        settings = PanelSettings(_Roster(), store)
        changed = settings.commit_model("tts-1")
        assert changed is ControlPush.CORRECT
        assert store.read_field("model") is None
        assert store.read_field("voice") is None

    @pytest.mark.parametrize(
        ("provider", "model"), [("espeak", None), ("elevenlabs", "eleven_v3")]
    )
    def test_model_yields_when_target_changed_mid_fetch(
        self, tmp_path: Path, provider: str, model: str | None
    ) -> None:
        """A provider or model change during the roster fetch wins over this commit."""

        class _RacingRoster:
            def voices(self, target: VoiceTarget) -> list[str]:
                assert target.provider == "elevenlabs"
                settings.replace_if_current(
                    settings.snapshot(),
                    settings.snapshot().with_provider(
                        provider, roster=("new",), model=model, voice="new"
                    ),
                    PanelNotice.silent(),
                )
                return ["matilda"]

        store = ConfigStore(tmp_path)
        settings = PanelSettings(_RacingRoster(), store)
        settings.replace_if_current(
            settings.snapshot(),
            PanelState.empty().with_provider(
                "elevenlabs", roster=("matilda",), model=None, voice="matilda"
            ),
            PanelNotice.silent(),
        )

        changed = settings.commit_model("tts-1-hd")

        assert changed is ControlPush.CORRECT
        assert store.read_field("model") is None
        assert store.read_field("voice") is None
        assert settings.scene().provider == provider
        assert settings.scene().model == model
        assert settings.snapshot().roster == ("new",)
