"""AWS Polly TTS provider."""

from __future__ import annotations

import logging
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast
from xml.sax.saxutils import escape

import boto3
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
)

from punt_vox.normalize import strip_vibe_tags
from punt_vox.output import OutputResolver
from punt_vox.providers.voice_resolver import VoiceResolver
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
)
from punt_vox.types_provider_errors import ProviderAuthError

if TYPE_CHECKING:
    from mypy_boto3_polly.client import PollyClient as PollyClientType
    from mypy_boto3_polly.literals import (
        EngineType,
        LanguageCodeType,
        VoiceIdType,
    )
    from mypy_boto3_polly.type_defs import VoiceTypeDef

logger = logging.getLogger(__name__)

__all__ = ["PollyProvider"]

# Polly codes that do not already start with their ISO 639-1 language.
_BCP47_TO_ISO = {"arb": "ar", "cmn-CN": "zh"}

# Default voice per language (ISO 639-1 -> lowercase Polly voice name).
_DEFAULT_VOICES: dict[str, str] = {
    "ar": "zeina",
    "da": "naja",
    "de": "vicki",
    "en": "joanna",
    "es": "lucia",
    "fi": "suvi",
    "fr": "lea",
    "hi": "aditi",
    "it": "carla",
    "ja": "takumi",
    "ko": "seoyeon",
    "nl": "lotte",
    "nb": "liv",
    "pl": "ewa",
    "pt": "vitoria",
    "ro": "carmen",
    "ru": "tatyana",
    "sv": "astrid",
    "tr": "filiz",
    "zh": "zhiyu",
}

# Engine preference order: best quality first.
_ENGINE_PREFERENCE: tuple[EngineType, ...] = (
    "neural",
    "generative",
    "long-form",
    "standard",
)

_AUTH_ERROR_CODES = frozenset(
    {
        "InvalidSignatureException",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "ExpiredTokenException",
        "UnrecognizedClientException",
        "InvalidClientTokenId",
        "AuthFailure",
        "AccessDeniedException",
    }
)


@contextmanager
def _translate_auth_errors() -> Generator[None]:
    """Expose rejected AWS credential chains without masking other SDK faults."""
    try:
        yield
    except NoCredentialsError as exc:
        raise ProviderAuthError("polly", None) from exc
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _AUTH_ERROR_CODES:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            raise ProviderAuthError("polly", status) from exc
        raise


def _infer_iso_from_bcp47(bcp47: str) -> str | None:
    """Convert a BCP 47 language code to ISO 639-1."""
    iso = _BCP47_TO_ISO.get(bcp47)
    if iso:
        return iso
    prefix = bcp47.split("-")[0]
    if len(prefix) == 2:
        return prefix
    return None


@dataclass(frozen=True)
class VoiceConfig:
    """Maps a Polly voice to its API parameters.

    Each VoiceConfig bundles a Polly voice ID with its required language
    code and engine type, eliminating the need for callers to know these
    implementation details.
    """

    voice_id: VoiceIdType
    language_code: LanguageCodeType
    engine: EngineType

    @classmethod
    def from_response(cls, voice: VoiceTypeDef) -> Self:
        """Require the metadata needed to make a Polly synthesis request."""
        voice_id = voice.get("Id")
        language_code = voice.get("LanguageCode")
        engines = voice.get("SupportedEngines")
        if voice_id is None or language_code is None or engines is None:
            msg = "Polly voice catalog entry lacks an ID, language, or engine list"
            raise ValueError(msg)
        return cls(voice_id, language_code, _best_engine(engines))


def _best_engine(supported: Sequence[EngineType]) -> EngineType:
    """Pick the best engine from a list of supported engines."""
    if not supported:
        msg = "Voice has no supported engines"
        raise ValueError(msg)
    for engine in _ENGINE_PREFERENCE:
        if engine in supported:
            return engine
    return supported[0]


class PollyProvider(TTSProvider):
    """AWS Polly TTS provider.

    Implements the TTSProvider protocol by wrapping boto3 Polly calls.
    Accepts an optional pre-configured boto3 Polly client for
    dependency injection in tests.
    """

    if TYPE_CHECKING:
        _client: PollyClientType
    _voices: VoiceResolver[VoiceConfig]

    def __new__(cls, boto_client: PollyClientType | None = None) -> Self:
        """Create a polly provider."""
        self = super().__new__(cls)
        if boto_client is not None:
            self._client = boto_client
        else:
            self._client = cast("PollyClientType", boto3.client("polly"))  # type: ignore[redundant-cast]  # pyright: ignore[reportUnknownMemberType]

        self._voices = VoiceResolver(
            self._fetch_voices,
            default_key="joanna",
            ttl_seconds=0,
            cooldown_seconds=60,
        )
        return self

    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return "polly"

    @property
    def default_voice(self) -> str:
        """Return the provider's default voice name."""
        return "joanna"

    @property
    def supports_expressive_tags(self) -> bool:
        """Return whether the selected model supports expressive tags."""
        return False

    def generate_audio(self, request: SynthesisRequest) -> SynthesisResult:
        """Synthesize the request into its resolved output path."""
        output_path = OutputResolver.resolve(request)
        return self.synthesize(request, output_path)

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize text to an MP3 file using AWS Polly.

        Resolves the voice name to Polly parameters internally, wraps
        the text in SSML with prosody rate, and writes the MP3 output.

        Raises:
            ProviderAuthError: If AWS credentials are missing, expired, or rejected.
        """
        text = strip_vibe_tags(request.text)

        resolved_voice = request.voice or self.default_voice
        voice_cfg = self._voices.resolve(resolved_voice)
        rate = request.rate if request.rate is not None else 100
        ssml_text = f'<speak><prosody rate="{rate}%">{escape(text)}</prosody></speak>'
        with _translate_auth_errors():
            response = self._client.synthesize_speech(
                Text=ssml_text,
                TextType="ssml",
                VoiceId=voice_cfg.voice_id,
                LanguageCode=voice_cfg.language_code,
                OutputFormat="mp3",
                Engine=voice_cfg.engine,
                SampleRate="22050",
            )

        logger.info(
            "API call: provider=polly, voice=%s, chars=%d",
            voice_cfg.voice_id,
            len(text),
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as f:
            f.write(response["AudioStream"].read())
        logger.info("Wrote %s", output_path)
        language = request.language or _infer_iso_from_bcp47(voice_cfg.language_code)
        return SynthesisResult(
            path=output_path,
            text=text,
            provider=AudioProviderId.polly,
            voice=voice_cfg.voice_id,
            language=language,
            metadata=request.metadata,
        )

    def resolve_voice(self, name: str, language: str | None = None) -> str:
        """Validate and resolve a voice name to its canonical form.

        Returns the Polly voice ID (e.g. "Joanna") if the name is valid.
        If language is provided, validates that the voice supports it.

        Raises:
            ValueError: If the requested language conflicts with the voice language.
        """
        cfg = self._voices.resolve(name)
        if (
            language is not None
            and _infer_iso_from_bcp47(cfg.language_code) != language
        ):
            msg = (
                f"Voice '{cfg.voice_id}' does not support language '{language}' "
                f"(supports {cfg.language_code})"
            )
            raise ValueError(msg)
        return cfg.voice_id

    def check_health(self) -> list[HealthCheck]:
        """Check AWS credentials and Polly API access."""
        return [self._check_aws_credentials(), self._check_polly_access()]

    @staticmethod
    def _check_aws_credentials() -> HealthCheck:
        try:
            sts: Any = boto3.client("sts")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            identity: Any = sts.get_caller_identity()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            account: str = identity["Account"]  # pyright: ignore[reportUnknownVariableType]
        except NoCredentialsError:
            detail = "not configured (run `aws configure`)"
        except NoRegionError:
            detail = "no region set (run `aws configure`)"
        except EndpointConnectionError:
            detail = "cannot reach AWS (check network)"
        except ClientError as e:
            detail = str(e)
        else:
            return HealthCheck(
                passed=True, message=f"AWS credentials (account: {account})"
            )
        return HealthCheck(passed=False, message=f"AWS credentials: {detail}")

    @staticmethod
    def _check_polly_access() -> HealthCheck:
        try:
            polly: Any = boto3.client("polly")  # pyright: ignore[reportUnknownMemberType]
            polly.describe_voices()
        except (NoCredentialsError, NoRegionError):
            detail = "skipped (no credentials)"
        except EndpointConnectionError:
            detail = "cannot reach AWS (check network)"
        except ClientError as e:
            detail = str(e)
        else:
            return HealthCheck(passed=True, message="AWS Polly access")
        return HealthCheck(passed=False, message=f"AWS Polly access: {detail}")

    def get_default_voice(self, language: str) -> str:
        """Get the default Polly voice for a language.

        Raises:
            ValueError: If no default voice is available for the language.
        """
        key = language.lower()
        voice = _DEFAULT_VOICES.get(key)
        if voice is None:
            supported = ", ".join(sorted(_DEFAULT_VOICES))
            msg = f"No default voice for language '{language}'. Supported: {supported}"
            raise ValueError(msg)
        return voice

    def list_voices(self, language: str | None = None) -> list[str]:
        """List available voices, optionally filtered by language."""
        all_names = self._voices.list_all()
        if language is None:
            return all_names
        # Need the VoiceConfig to filter by language -- access the cache
        return sorted(
            name
            for name in all_names
            if _infer_iso_from_bcp47(self._voices.resolve(name).language_code)
            == language
        )

    def infer_language_from_voice(self, voice: str) -> str | None:
        """Infer ISO 639-1 language from a Polly voice name."""
        cfg = self._voices.resolve(voice)
        return _infer_iso_from_bcp47(cfg.language_code)

    def _fetch_voices(self) -> dict[str, VoiceConfig]:
        """Fetch all voices from the Polly API.

        Paginates through all pages of the describe_voices response.
        Returns a dict mapping lowercase name -> VoiceConfig.
        """
        fresh: dict[str, VoiceConfig] = {}
        next_token: str | None = None

        while True:
            with _translate_auth_errors():
                resp = (
                    self._client.describe_voices(NextToken=next_token)
                    if next_token
                    else self._client.describe_voices()
                )

            for voice in resp["Voices"]:
                config = VoiceConfig.from_response(voice)
                fresh.setdefault(config.voice_id.lower(), config)

            next_token = resp.get("NextToken")
            if not next_token:
                break

        logger.debug("Fetched %d voices from Polly API", len(fresh))
        return fresh
