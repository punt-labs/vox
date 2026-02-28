"""Tests for punt_tts.providers.say."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import _get_valid_mp3_bytes  # pyright: ignore[reportPrivateUsage]

from punt_tts.providers.say import (
    SayProvider,
    SayVoiceConfig,
    _locale_to_iso,  # pyright: ignore[reportPrivateUsage]
    _rate_to_wpm,  # pyright: ignore[reportPrivateUsage]
)
from punt_tts.types import AudioProviderId, SynthesisRequest, VoiceNotFoundError


class TestSayVoiceConfig:
    def test_frozen(self) -> None:
        cfg = SayVoiceConfig(name="Fred", locale="en_US")
        with pytest.raises(AttributeError):
            cfg.name = "Samantha"  # type: ignore[misc]


class TestLocaleToIso:
    def test_english(self) -> None:
        assert _locale_to_iso("en_US") == "en"

    def test_german(self) -> None:
        assert _locale_to_iso("de_DE") == "de"

    def test_japanese(self) -> None:
        assert _locale_to_iso("ja_JP") == "ja"


class TestRateToWpm:
    def test_normal(self) -> None:
        assert _rate_to_wpm(100) == 175

    def test_slow(self) -> None:
        assert _rate_to_wpm(90) == 157

    def test_half(self) -> None:
        assert _rate_to_wpm(50) == 87

    def test_zero_clamps_to_one(self) -> None:
        assert _rate_to_wpm(0) == 1

    def test_fast(self) -> None:
        assert _rate_to_wpm(200) == 350


class TestSayProviderPlatformGuard:
    def test_non_darwin_raises(self) -> None:
        with (
            patch("punt_tts.providers.say.platform") as mock_platform,
            patch("punt_tts.providers.say.shutil"),
        ):
            mock_platform.system.return_value = "Linux"
            with pytest.raises(ValueError, match="requires macOS"):
                SayProvider()

    def test_no_say_command_raises(self) -> None:
        with (
            patch("punt_tts.providers.say.platform") as mock_platform,
            patch("punt_tts.providers.say.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Darwin"
            mock_shutil.which.return_value = None
            with pytest.raises(ValueError, match="say command not found"):
                SayProvider()

    def test_darwin_with_say_succeeds(self) -> None:
        with (
            patch("punt_tts.providers.say.platform") as mock_platform,
            patch("punt_tts.providers.say.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Darwin"
            mock_shutil.which.return_value = "/usr/bin/say"
            provider = SayProvider()
            assert provider.name == "say"


class TestSayProviderName:
    def test_name(self, say_provider: SayProvider) -> None:
        assert say_provider.name == "say"

    def test_default_voice(self, say_provider: SayProvider) -> None:
        assert say_provider.default_voice == "samantha"


class TestSayProviderResolveVoice:
    def test_resolve_cached_voice(self, say_provider: SayProvider) -> None:
        result = say_provider.resolve_voice("fred")
        assert result == "Fred"

    def test_resolve_case_insensitive(self, say_provider: SayProvider) -> None:
        result = say_provider.resolve_voice("FRED")
        assert result == "Fred"

    def test_resolve_german_voice(self, say_provider: SayProvider) -> None:
        result = say_provider.resolve_voice("anna")
        assert result == "Anna"

    def test_unknown_voice_raises(self, say_provider: SayProvider) -> None:
        import punt_tts.providers.say as say_mod

        say_mod.VOICES.clear()
        say_mod._voices_loaded = True  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(VoiceNotFoundError) as exc_info:
            say_provider.resolve_voice("nonexistent")
        assert exc_info.value.voice_name == "nonexistent"
        assert isinstance(exc_info.value.available, list)

    def test_matching_language(self, say_provider: SayProvider) -> None:
        result = say_provider.resolve_voice("fred", language="en")
        assert result == "Fred"

    def test_mismatching_language(self, say_provider: SayProvider) -> None:
        with pytest.raises(ValueError, match="does not support language 'de'"):
            say_provider.resolve_voice("fred", language="de")


class TestSayProviderSynthesize:
    def _mock_subprocess(self, mp3_bytes: bytes) -> MagicMock:
        """Create a side_effect for subprocess.run that handles say and ffmpeg."""

        def side_effect(
            args: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if args[0] == "say":
                # say writes an AIFF file; create a dummy
                output_idx = args.index("-o")
                aiff_path = Path(args[output_idx + 1])
                aiff_path.write_bytes(b"FORM\x00\x00\x00\x00AIFF")
                return subprocess.CompletedProcess(args, 0)
            if args[0] == "ffmpeg":
                # ffmpeg converts to MP3; write valid MP3 bytes
                output_path = Path(args[-1])
                output_path.write_bytes(mp3_bytes)
                return subprocess.CompletedProcess(args, 0, b"", b"")
            msg = f"Unexpected subprocess call: {args}"
            raise ValueError(msg)

        mock = MagicMock(side_effect=side_effect)
        return mock

    def test_synthesize_creates_file(
        self, say_provider: SayProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"

        mock = self._mock_subprocess(mp3_bytes)
        with patch("punt_tts.providers.say.subprocess.run", mock):
            result = say_provider.synthesize(
                SynthesisRequest(text="hello", voice="fred"), out
            )

        assert result.path == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_synthesize_result_metadata(
        self, say_provider: SayProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"

        mock = self._mock_subprocess(mp3_bytes)
        with patch("punt_tts.providers.say.subprocess.run", mock):
            result = say_provider.synthesize(
                SynthesisRequest(text="hello", voice="fred"), out
            )

        assert result.provider == AudioProviderId.say
        assert result.voice == "Fred"
        assert result.language == "en"
        assert result.text == "hello"

    def test_synthesize_say_args(
        self, say_provider: SayProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        mock_run = self._mock_subprocess(mp3_bytes)

        with patch("punt_tts.providers.say.subprocess.run", mock_run):
            say_provider.synthesize(
                SynthesisRequest(text="hello", voice="fred", rate=90), out
            )

        # First call should be say
        say_call = mock_run.call_args_list[0]
        say_args = say_call[0][0]
        assert say_args[0] == "say"
        assert "-v" in say_args
        assert say_args[say_args.index("-v") + 1] == "Fred"
        assert "-r" in say_args
        assert say_args[say_args.index("-r") + 1] == "157"  # 175 * 0.9
        assert "hello" in say_args

    def test_synthesize_ffmpeg_args(
        self, say_provider: SayProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        mock_run = self._mock_subprocess(mp3_bytes)

        with patch("punt_tts.providers.say.subprocess.run", mock_run):
            say_provider.synthesize(SynthesisRequest(text="hello", voice="fred"), out)

        # Second call should be ffmpeg
        ffmpeg_call = mock_run.call_args_list[1]
        ffmpeg_args = ffmpeg_call[0][0]
        assert ffmpeg_args[0] == "ffmpeg"
        assert "-codec:a" in ffmpeg_args
        assert "libmp3lame" in ffmpeg_args
        assert str(out) == ffmpeg_args[-1]

    def test_synthesize_cleans_up_aiff(
        self, say_provider: SayProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        aiff_paths: list[Path] = []

        original_mock = self._mock_subprocess(mp3_bytes)

        def tracking_side_effect(
            args: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if args[0] == "say":
                output_idx = args.index("-o")
                aiff_paths.append(Path(args[output_idx + 1]))
            result: subprocess.CompletedProcess[bytes] = original_mock(args, **kwargs)
            return result

        with patch(
            "punt_tts.providers.say.subprocess.run", side_effect=tracking_side_effect
        ):
            say_provider.synthesize(SynthesisRequest(text="hello", voice="fred"), out)

        assert len(aiff_paths) == 1
        assert not aiff_paths[0].exists()

    def test_synthesize_infers_language(
        self, say_provider: SayProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"

        mock = self._mock_subprocess(mp3_bytes)
        with patch("punt_tts.providers.say.subprocess.run", mock):
            result = say_provider.synthesize(
                SynthesisRequest(text="Hallo", voice="anna"), out
            )

        assert result.language == "de"

    def test_synthesize_explicit_language(
        self, say_provider: SayProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"

        mock = self._mock_subprocess(mp3_bytes)
        with patch("punt_tts.providers.say.subprocess.run", mock):
            result = say_provider.synthesize(
                SynthesisRequest(text="hello", voice="fred", language="en"), out
            )

        assert result.language == "en"


class TestSayProviderCheckHealth:
    def test_darwin_with_say(self) -> None:
        with (
            patch("punt_tts.providers.say.platform") as mock_platform,
            patch("punt_tts.providers.say.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Darwin"
            mock_shutil.which.return_value = "/usr/bin/say"
            provider = SayProvider()
            checks = provider.check_health()

            assert len(checks) == 1
            assert checks[0].passed
            assert "/usr/bin/say" in checks[0].message

    def test_non_darwin(self) -> None:
        with (
            patch("punt_tts.providers.say.platform") as mock_platform,
            patch("punt_tts.providers.say.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Darwin"
            mock_shutil.which.return_value = "/usr/bin/say"
            provider = SayProvider()

        # Now check health from a non-Darwin perspective
        with patch("punt_tts.providers.say.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            checks = provider.check_health()

        assert len(checks) == 1
        assert not checks[0].passed
        assert "requires macOS" in checks[0].message

    def test_darwin_without_say(self) -> None:
        with (
            patch("punt_tts.providers.say.platform") as mock_platform,
            patch("punt_tts.providers.say.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Darwin"
            mock_shutil.which.return_value = "/usr/bin/say"
            provider = SayProvider()

        # Now check health when say is not found
        with (
            patch("punt_tts.providers.say.platform") as mock_platform,
            patch("punt_tts.providers.say.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Darwin"
            mock_shutil.which.return_value = None
            checks = provider.check_health()

        assert len(checks) == 1
        assert not checks[0].passed
        assert "not found" in checks[0].message


class TestSayProviderGetDefaultVoice:
    def test_english(self, say_provider: SayProvider) -> None:
        assert say_provider.get_default_voice("en") == "samantha"

    def test_german(self, say_provider: SayProvider) -> None:
        assert say_provider.get_default_voice("de") == "anna"

    def test_unknown_language(self, say_provider: SayProvider) -> None:
        with pytest.raises(ValueError, match="No default voice"):
            say_provider.get_default_voice("xx")

    def test_case_insensitive(self, say_provider: SayProvider) -> None:
        assert say_provider.get_default_voice("EN") == "samantha"


class TestSayProviderListVoices:
    def test_all_voices(self, say_provider: SayProvider) -> None:
        voices = say_provider.list_voices()
        assert "fred" in voices
        assert "anna" in voices
        assert "samantha" in voices

    def test_filter_by_language(self, say_provider: SayProvider) -> None:
        voices = say_provider.list_voices(language="en")
        assert "fred" in voices
        assert "samantha" in voices
        assert "anna" not in voices

    def test_filter_by_german(self, say_provider: SayProvider) -> None:
        voices = say_provider.list_voices(language="de")
        assert "anna" in voices
        assert "fred" not in voices

    def test_sorted(self, say_provider: SayProvider) -> None:
        voices = say_provider.list_voices()
        assert voices == sorted(voices)


class TestSayProviderInferLanguage:
    def test_english_voice(self, say_provider: SayProvider) -> None:
        assert say_provider.infer_language_from_voice("fred") == "en"

    def test_german_voice(self, say_provider: SayProvider) -> None:
        assert say_provider.infer_language_from_voice("anna") == "de"


class TestAutoDetectSayFallback:
    def test_macos_no_keys_returns_say(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {},
                clear=True,
            ),
            patch("punt_tts.providers.platform") as mock_platform,
            patch(
                "punt_tts.providers.shutil.which",
                side_effect=lambda name: (  # pyright: ignore[reportUnknownLambdaType]
                    "/usr/bin/say" if name == "say" else None
                ),
            ),
        ):
            mock_platform.system.return_value = "Darwin"
            from punt_tts.providers import auto_detect_provider

            result = auto_detect_provider()
            assert result == "say"

    def test_linux_no_keys_no_espeak_returns_polly(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {},
                clear=True,
            ),
            patch("punt_tts.providers.platform") as mock_platform,
            patch("punt_tts.providers.shutil.which", return_value=None),
        ):
            mock_platform.system.return_value = "Linux"
            from punt_tts.providers import auto_detect_provider

            result = auto_detect_provider()
            assert result == "polly"

    def test_linux_no_keys_with_espeak_returns_espeak(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {},
                clear=True,
            ),
            patch("punt_tts.providers.platform") as mock_platform,
            patch(
                "punt_tts.providers.shutil.which",
                side_effect=lambda name: (  # pyright: ignore[reportUnknownLambdaType]
                    "/usr/bin/espeak-ng" if name == "espeak-ng" else None
                ),
            ),
        ):
            mock_platform.system.return_value = "Linux"
            from punt_tts.providers import auto_detect_provider

            result = auto_detect_provider()
            assert result == "espeak"

    def test_explicit_provider_overrides(self) -> None:
        with patch.dict(
            "os.environ",
            {"TTS_PROVIDER": "openai"},
            clear=True,
        ):
            from punt_tts.providers import auto_detect_provider

            result = auto_detect_provider()
            assert result == "openai"

    def test_elevenlabs_key_takes_priority(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {"ELEVENLABS_API_KEY": "test-key"},
                clear=True,
            ),
            patch("punt_tts.providers.platform") as mock_platform,
        ):
            mock_platform.system.return_value = "Darwin"
            from punt_tts.providers import auto_detect_provider

            result = auto_detect_provider()
            assert result == "elevenlabs"
