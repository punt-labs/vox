"""Shared test fixtures for punt-vox."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydub import AudioSegment

from punt_vox.core import TTSClient
from punt_vox.providers.elevenlabs import ElevenLabsProvider
from punt_vox.providers.espeak import EspeakProvider, EspeakVoiceConfig
from punt_vox.providers.openai import OpenAIProvider
from punt_vox.providers.polly import PollyProvider, VoiceConfig
from punt_vox.providers.say import SayProvider, SayVoiceConfig

# Test voice configs — constructed directly, no API call needed.
JOANNA = VoiceConfig(voice_id="Joanna", language_code="en-US", engine="neural")
HANS = VoiceConfig(voice_id="Hans", language_code="de-DE", engine="standard")
TATYANA = VoiceConfig(voice_id="Tatyana", language_code="ru-RU", engine="standard")
SEOYEON = VoiceConfig(voice_id="Seoyeon", language_code="ko-KR", engine="neural")


@pytest.fixture(autouse=True)
def _populate_voice_cache() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Pre-populate the voice cache so resolve_voice() never hits the Polly API.

    Tests that verify resolve_voice's API-calling behavior (test_polly_provider.py)
    explicitly clear VOICES and reset _voices_loaded before their test logic.
    """
    import punt_vox.providers.polly as polly

    saved_voices = dict(polly.VOICES)
    saved_loaded = polly._voices_loaded  # pyright: ignore[reportPrivateUsage]

    polly.VOICES.update(
        {
            "joanna": JOANNA,
            "hans": HANS,
            "tatyana": TATYANA,
            "seoyeon": SEOYEON,
        }
    )
    polly._voices_loaded = True  # pyright: ignore[reportPrivateUsage]

    yield

    polly.VOICES.clear()
    polly.VOICES.update(saved_voices)
    polly._voices_loaded = saved_loaded  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Provide a temporary output directory."""
    out = tmp_path / "output"
    out.mkdir()
    return out


def _generate_valid_mp3_bytes() -> bytes:
    """Generate minimal valid MP3 bytes using pydub."""
    silence = AudioSegment.silent(duration=50)
    buf = io.BytesIO()
    silence.export(buf, format="mp3")  # pyright: ignore[reportUnknownMemberType]
    return buf.getvalue()


# Cache to avoid regenerating on every call.
_VALID_MP3_BYTES: bytes | None = None


def _get_valid_mp3_bytes() -> bytes:
    global _VALID_MP3_BYTES
    if _VALID_MP3_BYTES is None:
        _VALID_MP3_BYTES = _generate_valid_mp3_bytes()  # pyright: ignore[reportConstantRedefinition]
    return _VALID_MP3_BYTES


def _make_polly_response() -> dict[str, Any]:
    """Create a mock Polly synthesize_speech response with valid MP3."""
    stream = MagicMock()
    stream.read.return_value = _get_valid_mp3_bytes()
    return {
        "AudioStream": stream,
        "ContentType": "audio/mpeg",
        "RequestCharacters": 10,
    }


@pytest.fixture
def mock_boto_client() -> MagicMock:
    """Create a mock boto3 Polly client that returns valid MP3 bytes."""
    client = MagicMock()
    client.synthesize_speech.side_effect = lambda **kwargs: _make_polly_response()  # pyright: ignore[reportUnknownLambdaType]
    return client


@pytest.fixture
def polly_provider(mock_boto_client: MagicMock) -> PollyProvider:
    """Create a PollyProvider with a mocked boto3 backend."""
    return PollyProvider(boto_client=mock_boto_client)


@pytest.fixture
def tts_client(polly_provider: PollyProvider) -> TTSClient:
    """Create a TTSClient backed by a mock PollyProvider."""
    return TTSClient(polly_provider)


def _make_openai_speech_response() -> MagicMock:
    """Create a mock OpenAI audio.speech.create() response with valid MP3."""
    response = MagicMock()
    response.content = _get_valid_mp3_bytes()
    return response


@pytest.fixture
def mock_openai_client() -> MagicMock:
    """Create a mock openai.OpenAI client that returns valid MP3 bytes."""
    client = MagicMock()
    client.audio.speech.create.side_effect = (
        lambda **kwargs: _make_openai_speech_response()  # pyright: ignore[reportUnknownLambdaType]
    )
    return client


@pytest.fixture
def openai_provider(mock_openai_client: MagicMock) -> OpenAIProvider:
    """Create an OpenAIProvider with a mocked OpenAI client."""
    return OpenAIProvider(client=mock_openai_client)


def _make_elevenlabs_stream_response() -> list[bytes]:
    """Create a mock ElevenLabs text_to_speech.stream() byte iterator."""
    return [_get_valid_mp3_bytes()]


@pytest.fixture
def mock_elevenlabs_client() -> MagicMock:
    """Create a mock ElevenLabs client that returns valid MP3 bytes."""
    client = MagicMock()
    client.text_to_speech.stream.side_effect = (
        lambda **kwargs: _make_elevenlabs_stream_response()  # pyright: ignore[reportUnknownLambdaType]
    )

    # Mock voices.get_all for voice resolution.
    # Real API returns names with descriptions
    # (e.g. "Matilda - Knowledgable, Professional").
    voice_matilda = MagicMock()
    voice_matilda.name = "Matilda - Knowledgable, Professional"
    voice_matilda.voice_id = "XrExE9yKIg1WjnnlVkGX"

    voice_drew = MagicMock()
    voice_drew.name = "Drew - eloquent, calm"
    voice_drew.voice_id = "29vD33N1CtxCmqQRPOHJ"

    voices_response = MagicMock()
    voices_response.voices = [voice_matilda, voice_drew]
    client.voices.get_all.return_value = voices_response

    # Mock subscription for health checks.
    subscription = MagicMock()
    subscription.tier = "free"
    subscription.character_count = 500
    subscription.character_limit = 10000
    client.user.subscription.get.return_value = subscription

    return client


@pytest.fixture(autouse=True)
def _populate_elevenlabs_voice_cache() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Pre-populate the ElevenLabs voice cache so resolve_voice() never hits the API."""
    import time

    import punt_vox.providers.elevenlabs as elevenlabs

    saved_voices = dict(elevenlabs.VOICES)
    saved_loaded_at = elevenlabs._voices_loaded_at  # pyright: ignore[reportPrivateUsage]
    saved_force_at = elevenlabs._voices_force_fetched_at  # pyright: ignore[reportPrivateUsage]

    elevenlabs.VOICES.update(
        {
            "matilda": "XrExE9yKIg1WjnnlVkGX",
            "drew": "29vD33N1CtxCmqQRPOHJ",
        }
    )
    # Set a recent timestamp so the cache appears fresh.
    elevenlabs._voices_loaded_at = time.monotonic()  # pyright: ignore[reportPrivateUsage]

    yield

    elevenlabs.VOICES.clear()
    elevenlabs.VOICES.update(saved_voices)
    elevenlabs._voices_loaded_at = saved_loaded_at  # pyright: ignore[reportPrivateUsage]
    elevenlabs._voices_force_fetched_at = saved_force_at  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def elevenlabs_provider(mock_elevenlabs_client: MagicMock) -> ElevenLabsProvider:
    """Create an ElevenLabsProvider with a mocked client."""
    return ElevenLabsProvider(client=mock_elevenlabs_client)


# ---------------------------------------------------------------------------
# macOS Say provider fixtures
# ---------------------------------------------------------------------------

# Test voice configs for say provider.
FRED = SayVoiceConfig(name="Fred", locale="en_US")
SAMANTHA = SayVoiceConfig(name="Samantha", locale="en_US")
ANNA_SAY = SayVoiceConfig(name="Anna", locale="de_DE")


@pytest.fixture(autouse=True)
def _populate_say_voice_cache() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Pre-populate the say voice cache so resolve_voice() never shells out."""
    import punt_vox.providers.say as say_mod

    saved_voices = dict(say_mod.VOICES)
    saved_loaded = say_mod._voices_loaded  # pyright: ignore[reportPrivateUsage]

    say_mod.VOICES.update(
        {
            "fred": FRED,
            "samantha": SAMANTHA,
            "anna": ANNA_SAY,
        }
    )
    say_mod._voices_loaded = True  # pyright: ignore[reportPrivateUsage]

    yield

    say_mod.VOICES.clear()
    say_mod.VOICES.update(saved_voices)
    say_mod._voices_loaded = saved_loaded  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def say_provider() -> SayProvider:
    """Create a SayProvider with platform and say command mocked."""
    with (
        patch("punt_vox.providers.say.platform") as mock_platform,
        patch("punt_vox.providers.say.shutil") as mock_shutil,
    ):
        mock_platform.system.return_value = "Darwin"
        mock_shutil.which.return_value = "/usr/bin/say"
        return SayProvider()


# ---------------------------------------------------------------------------
# espeak-ng provider fixtures
# ---------------------------------------------------------------------------

# Test voice configs for espeak provider.
ENGLISH_ESPEAK = EspeakVoiceConfig(name="en", language="en")
GERMAN_ESPEAK = EspeakVoiceConfig(name="de", language="de")
FRENCH_ESPEAK = EspeakVoiceConfig(name="fr", language="fr")


@pytest.fixture(autouse=True)
def _populate_espeak_voice_cache() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Pre-populate the espeak voice cache so resolve_voice() never shells out."""
    import punt_vox.providers.espeak as espeak_mod

    saved_voices = dict(espeak_mod.VOICES)
    saved_loaded = espeak_mod._voices_loaded  # pyright: ignore[reportPrivateUsage]

    espeak_mod.VOICES.update(
        {
            "english": ENGLISH_ESPEAK,
            "en": ENGLISH_ESPEAK,
            "german": GERMAN_ESPEAK,
            "de": GERMAN_ESPEAK,
            "french": FRENCH_ESPEAK,
            "fr": FRENCH_ESPEAK,
        }
    )
    espeak_mod._voices_loaded = True  # pyright: ignore[reportPrivateUsage]

    yield

    espeak_mod.VOICES.clear()
    espeak_mod.VOICES.update(saved_voices)
    espeak_mod._voices_loaded = saved_loaded  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def espeak_provider() -> EspeakProvider:
    """Create an EspeakProvider with espeak-ng binary mocked."""
    with patch(
        "punt_vox.providers.espeak._find_espeak_binary",
        return_value="/usr/bin/espeak-ng",
    ):
        return EspeakProvider()
