"""Tests for :mod:`punt_vox.panel.state`."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.panel.state import PanelState

if TYPE_CHECKING:
    from punt_vox.config import VoxConfig
    from punt_vox.voice_target import VoiceTarget


@final
class _FakeStore:
    """A minimal ``ConfigStore``-shaped double: one canned ``read()`` result."""

    def __init__(self, cfg: VoxConfig) -> None:
        self._cfg = cfg

    def read(self) -> VoxConfig:
        return self._cfg


@final
class _FakeClient:
    """A minimal ``VoxClientSync``-shaped double: one canned ``voices()`` result.

    Records the ``provider`` argument so tests can assert that ``PanelState.read``
    fetches the roster for the current session provider.
    """

    def __init__(self, voices: list[str]) -> None:
        self._voices = voices
        self.last_provider_arg: str | None = None
        self.last_model_arg: str | None = None
        self.call_count = 0

    def voices(self, provider: VoiceTarget) -> list[str]:
        self.last_provider_arg = provider.provider
        self.last_model_arg = provider.model
        self.call_count += 1
        return self._voices


def _config(provider: str | None = None, model: str | None = None) -> VoxConfig:
    from punt_vox.config import VoxConfig

    return VoxConfig(
        notify="y",
        speak="y",
        vibe_mode="auto",
        voice="roger",
        provider=provider,
        model=model,
        vibe=None,
        vibe_tags=None,
    )


class TestEmpty:
    def test_safe_defaults(self) -> None:
        state = PanelState.empty()
        assert state.notify == "n"
        assert state.speak == "y"
        assert state.voice is None
        assert state.roster == ()
        assert state.provider is None
        assert state.model is None


class TestRead:
    def test_reads_config_and_roster(self) -> None:
        state = PanelState.read(
            _FakeClient(["aria", "roger"]),
            _FakeStore(_config(provider="elevenlabs")),
        )
        assert state.notify == "y"
        assert state.speak == "y"
        assert state.voice == "roger"
        assert state.roster == ("aria", "roger")

    def test_reads_provider_and_model_from_config(self) -> None:
        client = _FakeClient(["marin"])
        state = PanelState.read(
            client,
            _FakeStore(_config(provider="openai", model="gpt-4o-mini-tts")),
        )
        assert state.provider == "openai"
        assert state.model == "gpt-4o-mini-tts"
        assert client.last_model_arg == state.model

    def test_roster_fetch_carries_the_current_provider(self) -> None:
        """PanelState.read asks voxd for the CURRENT provider's roster.

        A resync after a provider switch must see the new provider's voices,
        not the daemon-default provider's -- otherwise the panel keeps showing
        the previous roster until voxd restarts.
        """
        client = _FakeClient(["en", "en-us"])
        PanelState.read(client, _FakeStore(_config(provider="espeak")))
        assert client.last_provider_arg == "espeak"

    def test_roster_fetch_skipped_when_provider_unset(self) -> None:
        """An unset provider yields an empty roster; no daemon fetch is made.

        An unset provider yields an empty roster until a provider is chosen; the
        daemon is not asked to guess a provider.
        """
        client = _FakeClient(["aria"])
        state = PanelState.read(client, _FakeStore(_config()))
        assert client.call_count == 0
        assert state.roster == ()

    def test_whitespace_provider_is_normalised_to_none(self) -> None:
        """A hand-edited ``provider: "   "`` becomes ``None`` on the panel state.

        Stripping at the boundary ensures state.provider is either a nonempty
        stripped string or None, so whitespace cannot bypass SessionSpec or reach
        the voice-roster request.
        """
        client = _FakeClient(["aria"])
        state = PanelState.read(client, _FakeStore(_config(provider="   ")))
        assert state.provider is None
        assert state.roster == ()
        assert client.call_count == 0

    def test_empty_string_provider_is_normalised_to_none(self) -> None:
        """``provider: ""`` (the shipped shape of a fresh ``vox.md``) is ``None``.

        Same invariant as the whitespace case: no bare-empty provider
        crosses the boundary.
        """
        client = _FakeClient(["aria"])
        state = PanelState.read(client, _FakeStore(_config(provider="")))
        assert state.provider is None
        assert client.call_count == 0

    def test_padded_provider_is_normalised_before_the_roster_fetch(self) -> None:
        """``provider: "elevenlabs "`` reaches the wire as ``"elevenlabs"``.

        Without normalisation the padded name would flow to
        ``client.voices("elevenlabs ")`` and either fail on the daemon or
        return an empty roster for a name the user typed correctly.
        """
        client = _FakeClient(["aria", "roger"])
        state = PanelState.read(client, _FakeStore(_config(provider="  elevenlabs  ")))
        assert state.provider == "elevenlabs"
        assert client.last_provider_arg == "elevenlabs"

    def test_whitespace_model_is_normalised_to_none(self) -> None:
        """``model: "   "`` becomes ``None`` on the panel state.

        Same shape as the provider fix -- a padded model would slip a
        truthiness guard downstream and either fail an
        ``MODEL_TABLE.available`` lookup or (worse) satisfy a
        ``model or DEFAULT`` expression as a truthy real name. The strip
        collapses whitespace-only to absent so the downstream contract
        is one shape.
        """
        client = _FakeClient(["aria"])
        state = PanelState.read(
            client, _FakeStore(_config(provider="elevenlabs", model="   "))
        )
        assert state.model is None


class TestWithField:
    def test_with_notify_returns_a_new_state(self) -> None:
        before = PanelState.empty()
        after = before.with_notify("c")
        assert after.notify == "c"
        assert before.notify == "n"

    def test_with_speak_returns_a_new_state(self) -> None:
        after = PanelState.empty().with_speak("n")
        assert after.speak == "n"

    def test_with_voice_returns_a_new_state(self) -> None:
        after = PanelState.empty().with_voice("aria")
        assert after.voice == "aria"

    def test_with_provider_stores_the_cascaded_defaults(self) -> None:
        """The cascade rule stores exactly what the caller resolved."""
        before = PanelState(
            notify="n",
            speak="y",
            voice=None,
            roster=(),
            provider="elevenlabs",
            model="eleven_v3",
        )
        after = before.with_provider(
            "openai", roster=("alloy", "nova"), model="tts-1", voice="alloy"
        )
        assert after.provider == "openai"
        assert after.model == "tts-1"
        assert after.voice == "alloy"
        assert after.roster == ("alloy", "nova")
        # The old state is untouched.
        assert before.provider == "elevenlabs"
        assert before.model == "eleven_v3"

    def test_with_provider_modelless_default_stores_none(self) -> None:
        """A modelless provider carries ``model=None`` -- the caller's default."""
        before = PanelState(
            notify="y",
            speak="y",
            voice="benno",
            roster=("aria", "benno"),
            provider="elevenlabs",
            model="eleven_v3",
        )
        after = before.with_provider(
            "espeak", roster=("en", "en-us"), model=None, voice="en"
        )
        assert after.model is None
        assert after.voice == "en"
        assert after.roster == ("en", "en-us")

    def test_with_provider_stores_new_args_for_the_provider_already_held(self) -> None:
        """Re-asserting the held provider still stores the args handed with it.

        A provider setter must preserve a freshly fetched roster, model, and voice
        even when the provider name is unchanged. Only the caller can decide whether
        the complete update is a no-op.
        """
        before = PanelState(
            notify="y",
            speak="y",
            voice="benno",
            roster=("aria", "benno"),
            provider="elevenlabs",
            model="eleven_v3",
        )
        after = before.with_provider(
            "elevenlabs",
            roster=("only", "roger"),
            model="eleven_flash_v2_5",
            voice="only",
        )
        assert after.provider == "elevenlabs"
        assert after.roster == ("only", "roger")
        assert after.model == "eleven_flash_v2_5"
        assert after.voice == "only"


class TestScene:
    def test_scene_carries_the_state_fields(self) -> None:
        state = PanelState(
            notify="c",
            speak="n",
            voice="aria",
            roster=("aria",),
            provider="openai",
            model="tts-1",
        )
        scene = state.scene()
        assert scene.notify == "c"
        assert scene.speak == "n"
        assert scene.voice == "aria"
        assert scene.roster == ("aria",)
        assert scene.provider == "openai"
        assert scene.model == "tts-1"
