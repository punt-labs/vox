"""Tests for punt_vox.__main__ (typer CLI)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from punt_vox.__main__ import app

if TYPE_CHECKING:
    from click.testing import Result
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisResult,
)


def _mock_synthesize_result(path: Path, text: str = "hello") -> SynthesisResult:
    return SynthesisResult(
        path=path,
        text=text,
        provider=AudioProviderId.polly,
        voice="Joanna",
    )


def _make_mock_provider() -> MagicMock:
    """Create a mock TTSProvider."""
    provider = MagicMock()
    provider.name = "polly"
    provider.default_voice = "joanna"
    provider.resolve_voice.side_effect = lambda name, language=None: name.capitalize()  # pyright: ignore[reportUnknownLambdaType,reportUnknownMemberType]
    provider.infer_language_from_voice.return_value = "en"
    provider.get_default_voice.side_effect = lambda lang: "joanna"  # pyright: ignore[reportUnknownLambdaType,reportUnknownMemberType]
    provider.check_health.return_value = [
        HealthCheck(passed=True, message="AWS credentials (account: 123456789012)"),
        HealthCheck(passed=True, message="AWS Polly access"),
    ]
    return provider


_CLI = "punt_vox.__main__"


# ---------------------------------------------------------------------------
# unmute tests
# ---------------------------------------------------------------------------


class TestUnmuteCommand:
    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_unmute_basic(
        self,
        mock_get_provider: MagicMock,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        with patch("punt_vox.playback.enqueue"):
            result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0
        mock_instance.synthesize.assert_called_once()

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_unmute_custom_voice(
        self,
        mock_get_provider: MagicMock,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        with patch("punt_vox.playback.enqueue"):
            result = runner.invoke(app, ["unmute", "Hallo", "--voice", "hans"])

        assert result.exit_code == 0
        request = mock_instance.synthesize.call_args[0][0]
        assert request.voice == "hans"

    def test_unmute_no_text_fails(self) -> None:
        runner = CliRunner()
        with patch(f"{_CLI}.get_provider", return_value=_make_mock_provider()):
            result = runner.invoke(app, ["unmute"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# record tests
# ---------------------------------------------------------------------------


class TestRecordCommand:
    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_record_basic(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(app, ["record", "hello", "-o", str(out)])

        assert result.exit_code == 0
        mock_instance.synthesize.assert_called_once()

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_record_custom_voice(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "Hallo", "--voice", "hans", "-o", str(out)]
        )

        assert result.exit_code == 0
        request = mock_instance.synthesize.call_args[0][0]
        assert request.voice == "hans"

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_record_from_file(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(["hello", "world"]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.side_effect = [
            _mock_synthesize_result(out_dir / "a.mp3", "hello"),
            _mock_synthesize_result(out_dir / "b.mp3", "world"),
        ]

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "--from", str(input_file), "-d", str(out_dir)]
        )

        assert result.exit_code == 0
        assert mock_instance.synthesize.call_count == 2

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_record_voice_settings(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "record",
                "hello",
                "-o",
                str(out),
                "--stability",
                "0.5",
                "--similarity",
                "0.7",
                "--style",
                "0.3",
                "--speaker-boost",
            ],
        )

        assert result.exit_code == 0
        request = mock_instance.synthesize.call_args[0][0]
        assert request.stability == 0.5
        assert request.similarity == 0.7
        assert request.style == 0.3
        assert request.speaker_boost is True

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_record_with_language(
        self,
        mock_get_provider: MagicMock,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "test.mp3"
        provider = _make_mock_provider()
        provider.get_default_voice.side_effect = lambda lang: "vicki"  # pyright: ignore[reportUnknownLambdaType,reportUnknownMemberType]
        mock_get_provider.return_value = provider
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "Guten Tag", "--language", "de", "-o", str(out)]
        )

        assert result.exit_code == 0
        request = mock_instance.synthesize.call_args[0][0]
        assert request.voice == "vicki"
        assert request.language == "de"


# ---------------------------------------------------------------------------
# vibe tests
# ---------------------------------------------------------------------------


class TestVibeCommand:
    def test_vibe_mood(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        runner = CliRunner()
        result = runner.invoke(app, ["vibe", "excited"])
        assert result.exit_code == 0
        assert "excited" in result.output

    def test_vibe_auto(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        runner = CliRunner()
        result = runner.invoke(app, ["vibe", "auto"])
        assert result.exit_code == 0
        assert "auto" in result.output

    def test_vibe_off(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        runner = CliRunner()
        result = runner.invoke(app, ["vibe", "off"])
        assert result.exit_code == 0
        assert "off" in result.output


# ---------------------------------------------------------------------------
# notify/speak/voice tests
# ---------------------------------------------------------------------------


class TestNotifyCommand:
    def test_notify_y(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "y"])
        assert result.exit_code == 0
        assert "enabled" in result.output.lower()

    def test_notify_n(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "n"])
        assert result.exit_code == 0
        assert "disabled" in result.output.lower()

    def test_notify_c(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "c"])
        assert result.exit_code == 0
        assert "continuous" in result.output.lower()

    def test_notify_c_always_enables_speak(
        self, tmp_path: Path, monkeypatch: MagicMock
    ) -> None:
        """Continuous mode always sets speak=y, even if file exists."""
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        config.write_text('---\nspeak: "n"\nnotify: "n"\n---\n')
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "c"])
        assert result.exit_code == 0
        text = config.read_text()
        assert 'speak: "y"' in text
        assert 'notify: "c"' in text

    def test_notify_c_with_voice(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "c", "--voice", "matilda"])
        assert result.exit_code == 0
        text = config.read_text()
        assert 'voice: "matilda"' in text
        assert 'notify: "c"' in text
        assert 'speak: "y"' in text

    def test_notify_invalid(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        config = tmp_path / "config.md"
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "x"])
        assert result.exit_code == 1


class TestSpeakCommand:
    def test_speak_y(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["speak", "y"])
        assert result.exit_code == 0
        assert "voice on" in result.output.lower()

    def test_speak_n(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["speak", "n"])
        assert result.exit_code == 0
        assert "chimes" in result.output.lower()


class TestVoiceCommand:
    def test_voice(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["voice", "matilda"])
        assert result.exit_code == 0
        assert "matilda" in result.output.lower()


# ---------------------------------------------------------------------------
# version tests
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "vox" in result.output


# ---------------------------------------------------------------------------
# status tests
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        runner = CliRunner()
        with patch(f"{_CLI}.get_provider", return_value=_make_mock_provider()):
            result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "Provider" in result.output
        assert "Voice" in result.output


# ---------------------------------------------------------------------------
# main group tests
# ---------------------------------------------------------------------------


class TestMainGroup:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "vox" in result.output.lower()

    def test_unmute_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "--help"])
        assert result.exit_code == 0
        assert "voice" in result.output.lower()

    def test_verbose_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-v", "--help"])
        assert result.exit_code == 0

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_provider_flag(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_client_cls.return_value.synthesize.return_value = _mock_synthesize_result(
            out
        )

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["record", "hello", "--provider", "polly", "-o", str(out)],
        )
        assert result.exit_code == 0
        mock_get_provider.assert_called_once_with("polly", model=None)


# ---------------------------------------------------------------------------
# doctor tests
# ---------------------------------------------------------------------------


class TestDoctorCommand:
    def _run_doctor(
        self,
        tmp_path: Path,
        *,
        health_checks: list[HealthCheck] | None = None,
        ffmpeg_found: bool = True,
        uvx_found: bool = True,
        config_exists: bool = False,
        config_data: dict[str, object] | None = None,
        system_platform: str = "Darwin",
        espeak_found: str | None = None,
    ) -> Result:
        """Invoke doctor with controlled mocks."""
        provider = _make_mock_provider()
        if health_checks is not None:
            provider.check_health.return_value = health_checks

        def which_side_effect(name: str) -> str | None:
            if name == "ffmpeg" and ffmpeg_found:
                return "/opt/homebrew/bin/ffmpeg"
            if name == "uvx" and uvx_found:
                return "/usr/local/bin/uvx"
            if name in ("espeak-ng", "espeak") and espeak_found == name:
                return f"/usr/bin/{name}"
            return None

        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        if config_exists:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(config_data or {}))

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", side_effect=which_side_effect),
            patch(f"{_CLI}.get_provider", return_value=provider),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch(
                f"{_CLI}.default_output_dir",
                return_value=tmp_path / "audio",
            ),
            patch(f"{_CLI}.platform.system", return_value=system_platform),
        ):
            result = runner.invoke(app, ["doctor"])

        return result

    def test_all_required_pass(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path)
        assert result.exit_code == 0
        assert "✓ Python" in result.output
        assert "✓ Provider: polly" in result.output
        assert "✓ ffmpeg" in result.output

    def test_ffmpeg_missing_fails(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, ffmpeg_found=False)
        assert result.exit_code == 1
        assert "✗ ffmpeg" in result.output

    def test_uvx_missing_is_optional(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, uvx_found=False)
        assert result.exit_code == 0
        assert "○ uvx" in result.output

    def test_linux_no_keys_no_espeak_warns(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELEVENLABS_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            result = self._run_doctor(
                tmp_path, system_platform="Linux", espeak_found=None
            )
        assert result.exit_code == 0
        assert "espeak-ng/espeak: not found" in result.output


# ---------------------------------------------------------------------------
# install tests (marketplace)
# ---------------------------------------------------------------------------


class TestInstallCommand:
    def test_install_success(self) -> None:
        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value="/usr/bin/claude"),
            patch(f"{_CLI}.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, ["install"])

        assert result.exit_code == 0
        assert "Restart Claude Code" in result.output

    def test_install_no_claude(self) -> None:
        runner = CliRunner()
        with patch(f"{_CLI}.shutil.which", return_value=None):
            result = runner.invoke(app, ["install"])

        assert result.exit_code != 0


class TestUninstallCommand:
    def test_uninstall_success(self) -> None:
        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value="/usr/bin/claude"),
            patch(f"{_CLI}.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, ["uninstall"])

        assert result.exit_code == 0
        assert "Uninstalled." in result.output


# ---------------------------------------------------------------------------
# install-desktop tests (Claude Desktop MCP registration)
# ---------------------------------------------------------------------------

_UVX = "/usr/local/bin/uvx"


class TestInstallDesktopCommand:
    def test_creates_config_from_scratch(self, tmp_path: Path) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(
                f"{_CLI}.shutil.which",
                side_effect=lambda name: (  # pyright: ignore[reportUnknownLambdaType]
                    _UVX if name == "uvx" else "/usr/bin/say" if name == "say" else None
                ),
            ),
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=config_path,
            ),
            patch("punt_vox.providers.platform.system", return_value="Darwin"),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("ELEVENLABS_API_KEY", None)
            result = runner.invoke(
                app,
                ["install-desktop", "--output-dir", str(audio_dir)],
            )

        assert result.exit_code == 0
        assert config_path.exists()

        data = json.loads(config_path.read_text())
        server = data["mcpServers"]["tts"]
        assert server["command"] == _UVX
        assert server["args"] == ["--from", "punt-vox", "vox", "mcp"]
        assert server["env"]["VOX_OUTPUT_DIR"] == str(audio_dir)
        assert server["env"]["TTS_PROVIDER"] == "say"

    def test_preserves_other_servers(self, tmp_path: Path) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        config_path.parent.mkdir(parents=True)
        existing: dict[str, object] = {
            "mcpServers": {
                "other-server": {"command": "other", "args": []},
            }
        }
        config_path.write_text(json.dumps(existing))

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
        ):
            result = runner.invoke(
                app,
                ["install-desktop", "--output-dir", str(tmp_path / "audio")],
            )

        assert result.exit_code == 0
        data = json.loads(config_path.read_text())
        assert "other-server" in data["mcpServers"]
        assert "tts" in data["mcpServers"]


# ---------------------------------------------------------------------------
# Global flag tests
# ---------------------------------------------------------------------------


class TestGlobalFlags:
    def test_short_help_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-h"])
        assert result.exit_code == 0
        assert "Text-to-speech CLI." in result.output

    def test_quiet_suppresses_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-q", "version"])
        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_quiet_suppresses_status(self) -> None:
        runner = CliRunner()
        with patch(f"{_CLI}.get_provider", return_value=_make_mock_provider()):
            result = runner.invoke(app, ["-q", "status"])
        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_json_still_emits_with_quiet(self) -> None:
        runner = CliRunner()
        with patch(f"{_CLI}.get_provider", return_value=_make_mock_provider()):
            result = runner.invoke(app, ["--json", "-q", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "provider" in data

    def test_verbose_quiet_mutual_exclusion(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-v", "-q", "version"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output
