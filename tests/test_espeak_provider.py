"""Tests for punt_vox.providers.espeak."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import _get_valid_mp3_bytes  # pyright: ignore[reportPrivateUsage]

from punt_vox.providers.espeak import (
    EspeakProvider,
    EspeakVoiceConfig,
    _load_voices_from_system,  # pyright: ignore[reportPrivateUsage]
    _rate_to_wpm,  # pyright: ignore[reportPrivateUsage]
)
from punt_vox.types import AudioProviderId, SynthesisRequest, VoiceNotFoundError


class TestEspeakVoiceConfig:
    def test_frozen(self) -> None:
        cfg = EspeakVoiceConfig(name="english", language="en")
        with pytest.raises(AttributeError):
            cfg.name = "german"  # type: ignore[misc]


class TestRateToWpm:
    def test_normal(self) -> None:
        assert _rate_to_wpm(100) == 175

    def test_slow(self) -> None:
        assert _rate_to_wpm(90) == 157

    def test_half(self) -> None:
        assert _rate_to_wpm(50) == 87

    def test_zero_clamps_to_one(self) -> None:
        assert _rate_to_wpm(0) == 1


class TestEspeakProviderGuard:
    def test_no_binary_raises(self) -> None:
        with (
            patch(
                "punt_vox.providers.espeak._find_espeak_binary",
                return_value=None,
            ),
            pytest.raises(ValueError, match="espeak-ng or espeak not found"),
        ):
            EspeakProvider()

    def test_binary_found_succeeds(self) -> None:
        with patch(
            "punt_vox.providers.espeak._find_espeak_binary",
            return_value="/usr/bin/espeak-ng",
        ):
            provider = EspeakProvider()
            assert provider.name == "espeak"


class TestEspeakProviderName:
    def test_name(self, espeak_provider: EspeakProvider) -> None:
        assert espeak_provider.name == "espeak"


class TestEspeakBareIsoFallback:
    """Verify that _load_voices_from_system registers bare ISO 639-1 keys."""

    def test_bare_en_registered_from_qualified_variants(self) -> None:
        """When espeak-ng only has en-us and en-gb, bare 'en' is still registered."""
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        fake_output = (
            "Pty  Language  Age/Gender  VoiceName   File   Other Languages\n"
            " 5     en-us          M  english-us   other/en-us\n"
            " 5     en-gb          M  english-gb   other/en-gb\n"
            " 5     de             M  german       other/de\n"
        )
        with (
            patch(
                "punt_vox.providers.espeak._find_espeak_binary",
                return_value="/usr/bin/espeak-ng",
            ),
            patch(
                "punt_vox.providers.espeak.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["espeak-ng", "--voices"], 0, stdout=fake_output, stderr=""
                ),
            ),
        ):
            _load_voices_from_system()

        assert "en" in espeak_mod.VOICES
        assert espeak_mod.VOICES["en"].language == "en"
        # de should also get a bare entry
        assert "de" in espeak_mod.VOICES

    def test_bare_entry_wins_when_listed_first(self) -> None:
        """If bare 'en' appears before 'en-us', bare entry is kept."""
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        fake_output = (
            "Pty  Language  Age/Gender  VoiceName   File   Other Languages\n"
            " 5     en             M  english      default\n"
            " 5     en-us          M  english-us   other/en-us\n"
        )
        with (
            patch(
                "punt_vox.providers.espeak._find_espeak_binary",
                return_value="/usr/bin/espeak-ng",
            ),
            patch(
                "punt_vox.providers.espeak.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["espeak-ng", "--voices"], 0, stdout=fake_output, stderr=""
                ),
            ),
        ):
            _load_voices_from_system()

        assert espeak_mod.VOICES["en"].name == "en"

    def test_bare_entry_wins_when_listed_after_qualified(self) -> None:
        """If 'en-us' appears before bare 'en', bare entry still overrides."""
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod._voices_loaded = False  # pyright: ignore[reportPrivateUsage]

        fake_output = (
            "Pty  Language  Age/Gender  VoiceName   File   Other Languages\n"
            " 5     en-us          M  english-us   other/en-us\n"
            " 5     en             M  english      default\n"
        )
        with (
            patch(
                "punt_vox.providers.espeak._find_espeak_binary",
                return_value="/usr/bin/espeak-ng",
            ),
            patch(
                "punt_vox.providers.espeak.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["espeak-ng", "--voices"], 0, stdout=fake_output, stderr=""
                ),
            ),
        ):
            _load_voices_from_system()

        # Bare "en" should override the en-us fallback that was registered first
        assert espeak_mod.VOICES["en"].name == "en"


class TestEspeakDefaultVoiceDynamic:
    """Verify default_voice discovers what's actually installed."""

    def test_prefers_bare_en(self, espeak_provider: EspeakProvider) -> None:
        """With 'en' in VOICES, default_voice returns 'en'."""
        assert espeak_provider.default_voice == "en"

    def test_falls_back_to_en_us(self, espeak_provider: EspeakProvider) -> None:
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod.VOICES["en-us"] = EspeakVoiceConfig(name="en-us", language="en")
        espeak_mod.VOICES["de"] = EspeakVoiceConfig(name="de", language="de")

        assert espeak_provider.default_voice == "en-us"

    def test_falls_back_to_en_gb(self, espeak_provider: EspeakProvider) -> None:
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod.VOICES["en-gb"] = EspeakVoiceConfig(name="en-gb", language="en")

        assert espeak_provider.default_voice == "en-gb"

    def test_falls_back_to_first_en_variant(
        self, espeak_provider: EspeakProvider
    ) -> None:
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod.VOICES["en-au"] = EspeakVoiceConfig(name="en-au", language="en")

        assert espeak_provider.default_voice == "en-au"

    def test_falls_back_to_first_voice(self, espeak_provider: EspeakProvider) -> None:
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod.VOICES["de"] = EspeakVoiceConfig(name="de", language="de")

        assert espeak_provider.default_voice == "de"

    def test_empty_voices_returns_en(self, espeak_provider: EspeakProvider) -> None:
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()

        assert espeak_provider.default_voice == "en"


class TestEspeakProviderResolveVoice:
    def test_resolve_cached_voice(self, espeak_provider: EspeakProvider) -> None:
        result = espeak_provider.resolve_voice("english")
        assert result == "en"

    def test_resolve_by_language_code(self, espeak_provider: EspeakProvider) -> None:
        result = espeak_provider.resolve_voice("en")
        assert result == "en"

    def test_resolve_case_insensitive(self, espeak_provider: EspeakProvider) -> None:
        result = espeak_provider.resolve_voice("ENGLISH")
        assert result == "en"

    def test_unknown_voice_raises(self, espeak_provider: EspeakProvider) -> None:
        import punt_vox.providers.espeak as espeak_mod

        espeak_mod.VOICES.clear()
        espeak_mod._voices_loaded = True  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(VoiceNotFoundError) as exc_info:
            espeak_provider.resolve_voice("nonexistent")
        assert exc_info.value.voice_name == "nonexistent"
        assert isinstance(exc_info.value.available, list)

    def test_matching_language(self, espeak_provider: EspeakProvider) -> None:
        result = espeak_provider.resolve_voice("english", language="en")
        assert result == "en"

    def test_mismatching_language(self, espeak_provider: EspeakProvider) -> None:
        with pytest.raises(ValueError, match="does not support language 'de'"):
            espeak_provider.resolve_voice("english", language="de")


class TestEspeakProviderSynthesize:
    def _mock_subprocess(self, mp3_bytes: bytes) -> MagicMock:
        """Create a side_effect for subprocess.run."""

        def side_effect(
            args: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            cmd = args[0]
            if "espeak" in cmd:
                # espeak writes a WAV file
                w_idx = args.index("-w")
                wav_path = Path(args[w_idx + 1])
                wav_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
                return subprocess.CompletedProcess(args, 0)
            if cmd == "ffmpeg":
                output_path = Path(args[-1])
                output_path.write_bytes(mp3_bytes)
                return subprocess.CompletedProcess(args, 0, b"", b"")
            msg = f"Unexpected subprocess call: {args}"
            raise ValueError(msg)

        return MagicMock(side_effect=side_effect)

    def test_synthesize_creates_file(
        self, espeak_provider: EspeakProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        mock = self._mock_subprocess(mp3_bytes)

        with patch("punt_vox.providers.espeak.subprocess.run", mock):
            result = espeak_provider.synthesize(
                SynthesisRequest(text="hello", voice="en"), out
            )

        assert result.path == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_synthesize_result_metadata(
        self, espeak_provider: EspeakProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        mock = self._mock_subprocess(mp3_bytes)

        with patch("punt_vox.providers.espeak.subprocess.run", mock):
            result = espeak_provider.synthesize(
                SynthesisRequest(text="hello", voice="en"), out
            )

        assert result.provider == AudioProviderId.espeak
        assert result.voice == "en"
        assert result.language == "en"
        assert result.text == "hello"

    def test_synthesize_espeak_args(
        self, espeak_provider: EspeakProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        mock_run = self._mock_subprocess(mp3_bytes)

        with patch("punt_vox.providers.espeak.subprocess.run", mock_run):
            espeak_provider.synthesize(
                SynthesisRequest(text="hello", voice="en", rate=90),
                out,
            )

        espeak_call = mock_run.call_args_list[0]
        espeak_args = espeak_call[0][0]
        assert "espeak" in espeak_args[0]
        assert "-v" in espeak_args
        assert espeak_args[espeak_args.index("-v") + 1] == "en"
        assert "-s" in espeak_args
        assert espeak_args[espeak_args.index("-s") + 1] == "157"
        assert "hello" in espeak_args

    def test_synthesize_cleans_up_wav(
        self, espeak_provider: EspeakProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        wav_paths: list[Path] = []

        original_mock = self._mock_subprocess(mp3_bytes)

        def tracking_side_effect(
            args: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if "espeak" in args[0]:
                w_idx = args.index("-w")
                wav_paths.append(Path(args[w_idx + 1]))
            result: subprocess.CompletedProcess[bytes] = original_mock(args, **kwargs)
            return result

        with patch(
            "punt_vox.providers.espeak.subprocess.run",
            side_effect=tracking_side_effect,
        ):
            espeak_provider.synthesize(SynthesisRequest(text="hello", voice="en"), out)

        assert len(wav_paths) == 1
        assert not wav_paths[0].exists()

    def test_synthesize_infers_language(
        self, espeak_provider: EspeakProvider, tmp_output_dir: Path
    ) -> None:
        mp3_bytes = _get_valid_mp3_bytes()
        out = tmp_output_dir / "test.mp3"
        mock = self._mock_subprocess(mp3_bytes)

        with patch("punt_vox.providers.espeak.subprocess.run", mock):
            result = espeak_provider.synthesize(
                SynthesisRequest(text="Hallo", voice="german"), out
            )

        assert result.language == "de"


class TestEspeakProviderPlayDirectly:
    def test_spawns_without_w_flag(self, espeak_provider: EspeakProvider) -> None:
        mock = MagicMock(return_value=subprocess.CompletedProcess([], 0, b"", b""))
        with patch("punt_vox.providers.espeak.subprocess.run", mock):
            rc = espeak_provider.play_directly(
                SynthesisRequest(text="hello", voice="en")
            )

        assert rc == 0
        mock.assert_called_once()
        args = mock.call_args[0][0]
        assert "espeak" in args[0]
        assert "-w" not in args
        assert "-v" in args
        assert "hello" in args

    def test_nonzero_rc_returned(self, espeak_provider: EspeakProvider) -> None:
        mock = MagicMock(return_value=subprocess.CompletedProcess([], 3, b"", b"boom"))
        with patch("punt_vox.providers.espeak.subprocess.run", mock):
            rc = espeak_provider.play_directly(
                SynthesisRequest(text="hello", voice="en")
            )
        assert rc == 3

    def test_binary_missing_returns_error(
        self, espeak_provider: EspeakProvider
    ) -> None:
        with patch(
            "punt_vox.providers.espeak.subprocess.run",
            side_effect=FileNotFoundError("no espeak"),
        ):
            rc = espeak_provider.play_directly(
                SynthesisRequest(text="hello", voice="en")
            )
        assert rc == 1


class TestEspeakProviderCheckHealth:
    def test_binary_found(self) -> None:
        with patch(
            "punt_vox.providers.espeak._find_espeak_binary",
            return_value="/usr/bin/espeak-ng",
        ):
            provider = EspeakProvider()
            checks = provider.check_health()

        assert len(checks) == 2
        assert checks[0].passed
        assert "espeak-ng" in checks[0].message
        assert checks[1].passed
        assert "default voice: en (en)" in checks[1].message

    def test_default_voice_unavailable(self) -> None:
        """Health check reports failure when default voice can't be resolved."""
        import punt_vox.providers.espeak as espeak_mod

        with patch(
            "punt_vox.providers.espeak._find_espeak_binary",
            return_value="/usr/bin/espeak-ng",
        ):
            provider = EspeakProvider()

        # Empty VOICES so the fallback "en" from default_voice can't resolve
        espeak_mod.VOICES.clear()
        espeak_mod._voices_loaded = True  # pyright: ignore[reportPrivateUsage]

        with patch(
            "punt_vox.providers.espeak._find_espeak_binary",
            return_value="/usr/bin/espeak-ng",
        ):
            checks = provider.check_health()

        assert len(checks) == 2
        assert checks[0].passed
        assert not checks[1].passed
        assert "default voice not available" in checks[1].message

    def test_binary_not_found(self) -> None:
        with patch(
            "punt_vox.providers.espeak._find_espeak_binary",
            return_value="/usr/bin/espeak-ng",
        ):
            provider = EspeakProvider()

        with patch(
            "punt_vox.providers.espeak._find_espeak_binary",
            return_value=None,
        ):
            checks = provider.check_health()

        assert len(checks) == 1
        assert not checks[0].passed
        assert "not found" in checks[0].message


class TestEspeakProviderGetDefaultVoice:
    def test_english(self, espeak_provider: EspeakProvider) -> None:
        assert espeak_provider.get_default_voice("en") == "en"

    def test_german(self, espeak_provider: EspeakProvider) -> None:
        assert espeak_provider.get_default_voice("de") == "de"

    def test_unknown_language(self, espeak_provider: EspeakProvider) -> None:
        with pytest.raises(ValueError, match="No default voice"):
            espeak_provider.get_default_voice("xx")


class TestEspeakProviderListVoices:
    def test_all_voices(self, espeak_provider: EspeakProvider) -> None:
        voices = espeak_provider.list_voices()
        assert "english" in voices
        assert "en" in voices

    def test_filter_by_language(self, espeak_provider: EspeakProvider) -> None:
        voices = espeak_provider.list_voices(language="en")
        assert "english" in voices
        assert "en" in voices
        assert "german" not in voices

    def test_sorted(self, espeak_provider: EspeakProvider) -> None:
        voices = espeak_provider.list_voices()
        assert voices == sorted(voices)


class TestEspeakProviderInferLanguage:
    def test_english_voice(self, espeak_provider: EspeakProvider) -> None:
        assert espeak_provider.infer_language_from_voice("english") == "en"

    def test_german_voice(self, espeak_provider: EspeakProvider) -> None:
        assert espeak_provider.infer_language_from_voice("german") == "de"


class TestAutoDetectEspeakFallback:
    def test_linux_with_espeak_returns_espeak(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("punt_vox.providers.platform") as mock_platform,
            patch("punt_vox.providers.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Linux"
            mock_shutil.which.side_effect = lambda name: (  # pyright: ignore[reportUnknownLambdaType]
                "/usr/bin/espeak-ng" if name == "espeak-ng" else None
            )
            from punt_vox.providers import auto_detect_provider

            result = auto_detect_provider()
            assert result == "espeak"

    def test_linux_without_espeak_returns_polly(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("punt_vox.providers.platform") as mock_platform,
            patch("punt_vox.providers.shutil") as mock_shutil,
        ):
            mock_platform.system.return_value = "Linux"
            mock_shutil.which.return_value = None
            from punt_vox.providers import auto_detect_provider

            result = auto_detect_provider()
            assert result == "polly"
