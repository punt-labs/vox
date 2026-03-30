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


_CLI = "punt_vox.__main__"


# ---------------------------------------------------------------------------
# unmute tests
# ---------------------------------------------------------------------------


class TestUnmuteCommand:
    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_basic(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = "abc123"

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0
        mock_instance.synthesize.assert_called_once()
        call_kwargs = mock_instance.synthesize.call_args
        assert call_kwargs[0][0] == "hello"

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_custom_voice(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = "abc123"

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "Hallo", "--voice", "hans"])

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args
        assert call_kwargs[1]["voice"] == "hans"

    def test_unmute_no_text_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["unmute"])
        assert result.exit_code != 0

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_connection_error(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        from punt_vox.client import VoxdConnectionError

        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 1
        assert "not running" in result.output


# ---------------------------------------------------------------------------
# record tests
# ---------------------------------------------------------------------------


class TestRecordCommand:
    @patch(f"{_CLI}.VoxClientSync")
    def test_record_basic(self, mock_client_cls: MagicMock, tmp_path: Path) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10  # fake MP3

        runner = CliRunner()
        result = runner.invoke(app, ["record", "hello", "-o", str(out)])

        assert result.exit_code == 0
        mock_instance.record.assert_called_once()
        assert out.exists()

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_custom_voice(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "Hallo", "--voice", "hans", "-o", str(out)]
        )

        assert result.exit_code == 0
        call_kwargs = mock_instance.record.call_args
        assert call_kwargs[1]["voice"] == "hans"

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_from_file(self, mock_client_cls: MagicMock, tmp_path: Path) -> None:
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(["hello", "world"]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "--from", str(input_file), "-d", str(out_dir)]
        )

        assert result.exit_code == 0
        assert mock_instance.record.call_count == 2

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_voice_settings(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

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
        call_kwargs = mock_instance.record.call_args[1]
        assert call_kwargs["stability"] == 0.5
        assert call_kwargs["similarity"] == 0.7
        assert call_kwargs["style"] == 0.3
        assert call_kwargs["speaker_boost"] is True

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_with_language(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "Guten Tag", "--language", "de", "-o", str(out)]
        )

        assert result.exit_code == 0
        call_kwargs = mock_instance.record.call_args[1]
        assert call_kwargs["language"] == "de"

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_connection_error(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from punt_vox.client import VoxdConnectionError

        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["record", "hello", "-o", str(out)])

        assert result.exit_code == 1
        assert "not running" in result.output


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
    @patch(f"{_CLI}.VoxClientSync")
    def test_status_daemon_running(
        self, mock_client_cls: MagicMock, tmp_path: Path, monkeypatch: MagicMock
    ) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        mock_instance = mock_client_cls.return_value
        mock_instance.health.return_value = {"provider": "elevenlabs"}

        runner = CliRunner()
        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "Daemon" in result.output
        assert "running" in result.output

    @patch(f"{_CLI}.VoxClientSync")
    def test_status_daemon_not_running(
        self, mock_client_cls: MagicMock, tmp_path: Path, monkeypatch: MagicMock
    ) -> None:
        import punt_vox.config as cfg
        from punt_vox.client import VoxdConnectionError

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        mock_instance = mock_client_cls.return_value
        mock_instance.health.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "not running" in result.output


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

    @patch(f"{_CLI}.VoxClientSync")
    def test_provider_flag(self, mock_client_cls: MagicMock, tmp_path: Path) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["record", "hello", "--provider", "polly", "-o", str(out)],
        )
        assert result.exit_code == 0
        call_kwargs = mock_instance.record.call_args[1]
        assert call_kwargs["provider"] == "polly"


# ---------------------------------------------------------------------------
# doctor tests
# ---------------------------------------------------------------------------


class TestDoctorCommand:
    def _run_doctor(
        self,
        tmp_path: Path,
        *,
        ffmpeg_found: bool = True,
        uvx_found: bool = True,
        config_exists: bool = False,
        config_data: dict[str, object] | None = None,
        system_platform: str = "Darwin",
        espeak_found: str | None = None,
        daemon_healthy: bool = True,
    ) -> Result:
        """Invoke doctor with controlled mocks."""

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

        mock_client = MagicMock()
        if daemon_healthy:
            mock_client.health.return_value = {
                "provider": "elevenlabs",
                "active_sessions": 2,
                "port": 8421,
            }
        else:
            from punt_vox.client import VoxdConnectionError

            mock_client.health.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", side_effect=which_side_effect),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
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
        assert "\u2713 Python" in result.output
        assert "\u2713 ffmpeg" in result.output
        assert "Daemon: running" in result.output

    def test_ffmpeg_missing_fails(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, ffmpeg_found=False)
        assert result.exit_code == 1
        assert "\u2717 ffmpeg" in result.output

    def test_uvx_missing_is_optional(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, uvx_found=False)
        assert result.exit_code == 0
        assert "\u25cb uvx" in result.output

    def test_daemon_not_running_fails(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, daemon_healthy=False)
        assert result.exit_code == 1
        assert "Daemon: not running" in result.output

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
            patch("punt_vox.service.install", return_value="voxd running"),
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

    @patch(f"{_CLI}.VoxClientSync")
    def test_quiet_suppresses_status(self, mock_client_cls: MagicMock) -> None:
        from punt_vox.client import VoxdConnectionError

        mock_instance = mock_client_cls.return_value
        mock_instance.health.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["-q", "status"])
        assert result.exit_code == 0
        assert result.output.strip() == ""

    @patch(f"{_CLI}.VoxClientSync")
    def test_json_still_emits_with_quiet(self, mock_client_cls: MagicMock) -> None:
        mock_instance = mock_client_cls.return_value
        mock_instance.health.return_value = {"provider": "polly"}

        runner = CliRunner()
        result = runner.invoke(app, ["--json", "-q", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "provider" in data

    def test_verbose_quiet_mutual_exclusion(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-v", "-q", "version"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output
