"""Tests for punt_vox.__main__ (typer CLI)."""

from __future__ import annotations

import json
import os
import subprocess
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
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc123")

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
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc123")

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

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_api_key_forwards_to_client(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        """--api-key value is forwarded to client.synthesize(api_key=...).

        Per-call key isolation — the user provides a billing-attribution
        key on this single call. Regression guard for vox-a3e: verifies
        the CLI surface that was missing prior to this commit.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(
            app, ["unmute", "billable work", "--api-key", "sk_project_a"]
        )

        assert result.exit_code == 0
        mock_instance.synthesize.assert_called_once()
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_project_a"

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_api_key_not_echoed_to_output(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        """The api key must never appear in stdout, stderr, or logs.

        Security invariant: a secret passed on the command line should
        survive only long enough to reach voxd over the local WebSocket.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        secret = "sk_SECRET_never_echo"
        result = runner.invoke(app, ["unmute", "hello world", "--api-key", secret])

        assert result.exit_code == 0
        assert secret not in result.output
        # JSON mode also must not echo it — the payload only includes id.
        result_json = runner.invoke(
            app,
            ["--json", "unmute", "hello world", "--api-key", secret],
        )
        assert result_json.exit_code == 0
        assert secret not in result_json.output

    def test_unmute_api_key_empty_raises(self) -> None:
        """An empty --api-key is a user error, not a silent fallback."""
        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello", "--api-key", ""])
        assert result.exit_code != 0
        assert "cannot be empty" in result.output

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_no_api_key_omits_kwarg(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        """Without --api-key, the client is called with api_key=None.

        A None value lets voxd fall back to the keys.env default.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] is None


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
        daemon_version: str | None = "4.2.0",
        installed_version: str = "4.2.0",
    ) -> Result:
        """Invoke doctor with controlled mocks.

        ``daemon_version`` and ``installed_version`` default to the same
        value so the mismatch warning does not fire unless the test
        explicitly diverges them. Pass ``daemon_version=None`` to
        simulate a pre-upgrade daemon that predates the health-version
        field.
        """

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
            health_payload: dict[str, object] = {
                "provider": "elevenlabs",
                "active_sessions": 2,
                "port": 8421,
            }
            if daemon_version is not None:
                health_payload["daemon_version"] = daemon_version
            mock_client.health.return_value = health_payload
        else:
            from punt_vox.client import VoxdConnectionError

            mock_client.health.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", side_effect=which_side_effect),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(
                f"{_CLI}._installed_wheel_version",
                return_value=installed_version,
            ),
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

    def test_matching_versions_passes_without_warning(self, tmp_path: Path) -> None:
        """Daemon version == installed wheel version: green checkmark, no warn."""
        result = self._run_doctor(
            tmp_path,
            daemon_version="4.2.0",
            installed_version="4.2.0",
        )
        assert result.exit_code == 0
        assert "\u2713 Daemon: running" in result.output
        assert "version 4.2.0" in result.output
        assert "\u26a0" not in result.output

    def test_mismatched_versions_warns_without_failing(self, tmp_path: Path) -> None:
        """Running daemon older than installed wheel: warning, exit code 0.

        This is the vox-nmb regression guard. A stale voxd survived
        v4.2.0 release-day smoke tests because doctor only checked
        reachability, not version alignment. Doctor now warns but does
        not fail — the daemon is still functional, just out of date.
        """
        result = self._run_doctor(
            tmp_path,
            daemon_version="4.1.1",
            installed_version="4.2.0",
        )
        assert result.exit_code == 0
        assert "\u26a0 Daemon: running" in result.output
        assert "version 4.1.1" in result.output
        assert "wheel has 4.2.0" in result.output
        assert "vox daemon restart" in result.output
        # Summary line should flag the warning count.
        assert "1 warning" in result.output

    def test_mismatched_versions_json_mode(self, tmp_path: Path) -> None:
        """--json output includes the warned count for machine consumption."""
        result = self._run_doctor(
            tmp_path,
            daemon_version="4.1.1",
            installed_version="4.2.0",
        )
        # The helper invokes without --json; re-run with explicit --json flag.
        runner = CliRunner()

        def which_side_effect(name: str) -> str | None:
            if name == "ffmpeg":
                return "/opt/homebrew/bin/ffmpeg"
            if name == "uvx":
                return "/usr/local/bin/uvx"
            return None

        mock_client = MagicMock()
        mock_client.health.return_value = {
            "provider": "elevenlabs",
            "active_sessions": 2,
            "port": 8421,
            "daemon_version": "4.1.1",
        }

        with (
            patch(f"{_CLI}.shutil.which", side_effect=which_side_effect),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}._installed_wheel_version", return_value="4.2.0"),
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=tmp_path / "nope.json",
            ),
            patch(
                f"{_CLI}.default_output_dir",
                return_value=tmp_path / "audio",
            ),
            patch(f"{_CLI}.platform.system", return_value="Darwin"),
        ):
            result = runner.invoke(app, ["--json", "doctor"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["warned"] >= 1

    def test_pre_version_daemon_falls_back_to_pass(self, tmp_path: Path) -> None:
        """An older daemon that lacks daemon_version still reports PASS.

        Daemons built before commit 2 do not include daemon_version in
        their health payload. Doctor must not treat that as a mismatch
        — it cannot tell the version, so it cannot warn intelligently.
        Fall back to the existing "Daemon: running" pass.
        """
        result = self._run_doctor(
            tmp_path,
            daemon_version=None,
            installed_version="4.2.0",
        )
        assert result.exit_code == 0
        assert "\u2713 Daemon: running" in result.output
        assert "\u26a0" not in result.output


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


# ---------------------------------------------------------------------------
# daemon restart tests
# ---------------------------------------------------------------------------


class TestDaemonRestartCommand:
    """``vox daemon restart`` cycles voxd via the service manager.

    Regression guard for vox-nmb: a stale voxd survived v4.2.0's
    release-day verification because doctor reported "Daemon: running"
    without checking whether the running process matched the on-disk
    wheel. The restart command is the second half of the fix (the first
    half is the version-mismatch warning in commit 3).
    """

    def test_refuses_to_run_as_root(self) -> None:
        """Refuse ``sudo vox daemon restart`` — sudo is invoked internally."""
        runner = CliRunner()
        with patch(f"{_CLI}.os.geteuid", return_value=0):
            result = runner.invoke(app, ["daemon", "restart"])
        assert result.exit_code != 0
        assert "without sudo" in result.output or "not root" in result.output

    def test_unsupported_platform_fails(self) -> None:
        """Windows (or anything else) raises SystemExit from detect_platform."""
        runner = CliRunner()
        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch(
                "punt_vox.service.detect_platform",
                side_effect=SystemExit("Unsupported platform: Windows."),
            ),
        ):
            result = runner.invoke(app, ["daemon", "restart"])
        assert result.exit_code != 0

    def test_linux_restart_sequence(self) -> None:
        """Linux path: stop, ensure port free, start, verify via health."""
        runner = CliRunner()

        mock_client = MagicMock()
        mock_client.health.return_value = {"pid": 42, "port": 8421}

        calls: list[tuple[str, ...]] = []

        def fake_run(
            argv: list[str],
            *,
            check: bool = False,
        ) -> MagicMock:
            calls.append(tuple(argv))
            return MagicMock(returncode=0)

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop") as mock_stop,
            patch("punt_vox.service._ensure_port_free") as mock_free,
            patch(f"{_CLI}.subprocess.run", side_effect=fake_run),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 0, result.output
        mock_stop.assert_called_once()
        mock_free.assert_called_once()
        # The command must have started voxd via systemctl.
        assert calls == [("sudo", "systemctl", "start", "voxd")]
        assert "pid=42" in result.output
        assert "port 8421" in result.output

    def test_macos_restart_sequence(self) -> None:
        """macOS path: unload, ensure port free, load + kickstart."""
        runner = CliRunner()

        mock_client = MagicMock()
        mock_client.health.return_value = {"pid": 99, "port": 8421}

        calls: list[tuple[str, ...]] = []

        def fake_run(
            argv: list[str],
            *,
            check: bool = False,
        ) -> MagicMock:
            calls.append(tuple(argv))
            return MagicMock(returncode=0)

        with (
            patch(f"{_CLI}.os.geteuid", return_value=501),
            patch("punt_vox.service.detect_platform", return_value="macos"),
            patch("punt_vox.service._launchd_stop") as mock_stop,
            patch("punt_vox.service._ensure_port_free") as mock_free,
            patch(f"{_CLI}.subprocess.run", side_effect=fake_run),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 0, result.output
        mock_stop.assert_called_once()
        mock_free.assert_called_once()
        assert len(calls) == 2
        # First call: launchctl load -w
        assert calls[0][0] == "sudo"
        assert calls[0][1] == "launchctl"
        assert calls[0][2] == "load"
        assert calls[0][3] == "-w"
        assert calls[0][4] == "/Library/LaunchDaemons/com.punt-labs.voxd.plist"
        # Second call: launchctl kickstart -k
        assert calls[1] == (
            "sudo",
            "launchctl",
            "kickstart",
            "-k",
            "system/com.punt-labs.voxd",
        )
        assert "pid=99" in result.output

    def test_health_retry_before_success(self) -> None:
        """Daemon takes two poll cycles to come back — restart still succeeds."""
        from punt_vox.client import VoxdConnectionError

        runner = CliRunner()

        mock_client = MagicMock()
        # First two polls fail, third succeeds.
        mock_client.health.side_effect = [
            VoxdConnectionError("not yet"),
            VoxdConnectionError("not yet"),
            {"pid": 7, "port": 8421},
        ]

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", return_value=MagicMock(returncode=0)),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.time.sleep") as mock_sleep,
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 0, result.output
        assert mock_client.health.call_count == 3
        assert mock_sleep.call_count == 2
        assert "pid=7" in result.output

    def test_start_subprocess_failure_exits_with_log_hint(self) -> None:
        """systemctl start failure exits 1 and points at the voxd log."""
        runner = CliRunner()

        def fake_run(
            argv: list[str],
            *,
            check: bool = False,
        ) -> MagicMock:
            raise subprocess.CalledProcessError(1, argv)

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 1
        assert "voxd.log" in result.output

    def test_daemon_never_comes_back_exits_with_log_hint(self) -> None:
        """Health never succeeds within the 5s window — exit 1 with log hint."""
        from punt_vox.client import VoxdConnectionError

        runner = CliRunner()

        mock_client = MagicMock()
        mock_client.health.side_effect = VoxdConnectionError("refused")

        # Fake time.monotonic to immediately expire the deadline.
        ticks = iter([0.0, 0.0, 100.0])

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", return_value=MagicMock(returncode=0)),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.time.monotonic", side_effect=lambda: next(ticks)),
            patch(f"{_CLI}.time.sleep"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 1
        assert "voxd.log" in result.output
        assert "refused" in result.output
