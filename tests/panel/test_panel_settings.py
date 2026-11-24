"""Unit tests for the panel's shared settings and cascade commits."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final

from punt_vox.config import ConfigStore
from punt_vox.panel.control_push import ControlPush
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.panel_settings import PanelSettings
from punt_vox.panel.state import PanelState

if TYPE_CHECKING:
    from pathlib import Path


@final
class _Roster:
    def voices(self, provider: str) -> list[str]:
        assert provider == "openai"
        return ["alloy", "nova"]


class TestPanelSettings:
    def test_provider_cascades_model_and_voice(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path)
        settings = PanelSettings(_Roster(), store, logging.getLogger(__name__))

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
        settings = PanelSettings(_Roster(), store, logging.getLogger(__name__))
        settings.replace(
            PanelState.empty().with_provider(
                "openai", roster=("alloy", "nova"), model="tts-1", voice="nova"
            ),
            PanelNotice.silent(),
        )

        assert settings.commit_model("tts-1-hd") is ControlPush.REFRESH

        assert store.read_field("model") == "tts-1-hd"
        assert store.read_field("voice") == "alloy"
        assert settings.snapshot().voice == "alloy"

    def test_provider_yields_when_another_thread_won_the_race(
        self, tmp_path: Path
    ) -> None:
        """A provider change during the roster fetch wins over this commit."""

        class _RacingRoster:
            def voices(self, provider: str) -> list[str]:
                assert provider == "espeak"
                settings.replace(
                    settings.snapshot().with_provider(
                        "openai", roster=("alloy",), model="tts-1", voice="alloy"
                    ),
                    PanelNotice.silent(),
                )
                return ["benno"]

        store = ConfigStore(tmp_path)
        settings = PanelSettings(_RacingRoster(), store, logging.getLogger(__name__))
        settings.replace(
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
        settings = PanelSettings(_Roster(), store, logging.getLogger(__name__))
        changed = settings.commit_model("tts-1")
        assert changed is ControlPush.CORRECT
        assert store.read_field("model") is None
        assert store.read_field("voice") is None

    def test_model_yields_when_provider_changed_mid_fetch(self, tmp_path: Path) -> None:
        """A provider change during the roster fetch wins over this commit."""

        class _RacingRoster:
            def voices(self, provider: str) -> list[str]:
                assert provider == "elevenlabs"
                settings.replace(
                    settings.snapshot().with_provider(
                        "espeak", roster=("en",), model=None, voice="en"
                    ),
                    PanelNotice.silent(),
                )
                return ["matilda"]

        store = ConfigStore(tmp_path)
        settings = PanelSettings(_RacingRoster(), store, logging.getLogger(__name__))
        settings.replace(
            PanelState.empty().with_provider(
                "elevenlabs", roster=("matilda",), model=None, voice="matilda"
            ),
            PanelNotice.silent(),
        )

        changed = settings.commit_model("tts-1-hd")

        assert changed is ControlPush.CORRECT
        assert store.read_field("model") is None
        assert store.read_field("voice") is None
        assert settings.scene().provider == "espeak"
