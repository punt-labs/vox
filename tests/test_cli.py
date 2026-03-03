"""Tests for punt_vox.cli."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner, Result

from punt_vox.cli import main
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    MergeStrategy,
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


_CLI = "punt_vox.cli"


class TestSynthesizeCommand:
    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_synthesize_basic(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(main, ["synthesize", "hello", "-o", str(out)])

        assert result.exit_code == 0
        assert str(out) in result.output
        mock_instance.synthesize.assert_called_once()

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_synthesize_custom_voice(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize", "Hallo", "--voice", "hans", "-o", str(out)],
        )

        assert result.exit_code == 0
        call_args = mock_instance.synthesize.call_args
        request = call_args[0][0]
        assert request.voice == "hans"

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_synthesize_custom_rate(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize", "hello", "--rate", "100", "-o", str(out)],
        )

        assert result.exit_code == 0
        call_args = mock_instance.synthesize.call_args
        request = call_args[0][0]
        assert request.rate == 100

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_synthesize_voice_settings(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize",
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
        call_args = mock_instance.synthesize.call_args
        request = call_args[0][0]
        assert request.stability == 0.5
        assert request.similarity == 0.7
        assert request.style == 0.3
        assert request.speaker_boost is True

    @patch(f"{_CLI}.get_provider")
    def test_synthesize_invalid_voice(self, mock_get_provider: MagicMock) -> None:
        provider = _make_mock_provider()
        provider.resolve_voice.side_effect = ValueError("Unknown voice 'nonexistent'")
        mock_get_provider.return_value = provider

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize", "hello", "--voice", "nonexistent"],
        )
        assert result.exit_code != 0

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_synthesize_with_language(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        provider = _make_mock_provider()
        provider.get_default_voice.side_effect = lambda lang: "vicki"  # pyright: ignore[reportUnknownLambdaType,reportUnknownMemberType]
        mock_get_provider.return_value = provider
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize", "Guten Tag", "--language", "de", "-o", str(out)],
        )

        assert result.exit_code == 0
        request = mock_instance.synthesize.call_args[0][0]
        assert request.voice == "vicki"
        assert request.language == "de"

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_synthesize_lang_shorthand(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize", "hello", "--lang", "en", "-o", str(out)],
        )
        assert result.exit_code == 0

    @patch(f"{_CLI}.get_provider")
    def test_synthesize_invalid_language(self, mock_get_provider: MagicMock) -> None:
        mock_get_provider.return_value = _make_mock_provider()

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize", "hello", "--language", "xxx"],
        )
        assert result.exit_code != 0

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_synthesize_voice_and_language(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = _mock_synthesize_result(out)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize",
                "Hallo",
                "--voice",
                "hans",
                "--language",
                "de",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0
        request = mock_instance.synthesize.call_args[0][0]
        assert request.voice == "hans"
        assert request.language == "de"


class TestSynthesizeBatchCommand:
    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_batch_basic(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(["hello", "world"]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize_batch.return_value = [
            _mock_synthesize_result(out_dir / "a.mp3", "hello"),
            _mock_synthesize_result(out_dir / "b.mp3", "world"),
        ]

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize-batch", str(input_file), "-d", str(out_dir)],
        )

        assert result.exit_code == 0
        mock_instance.synthesize_batch.assert_called_once()

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_batch_with_merge(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(["hello", "world"]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize_batch.return_value = [
            _mock_synthesize_result(out_dir / "merged.mp3", "hello | world"),
        ]

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize-batch",
                str(input_file),
                "-d",
                str(out_dir),
                "--merge",
            ],
        )

        assert result.exit_code == 0
        call_args = mock_instance.synthesize_batch.call_args
        assert call_args[0][2] == MergeStrategy.ONE_FILE_PER_BATCH


class TestSynthesizePairCommand:
    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_pair_basic(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "pair.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize_pair.return_value = SynthesisResult(
            path=out,
            text="strong | stark",
            provider=AudioProviderId.polly,
            voice="joanna+hans",
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize-pair",
                "strong",
                "stark",
                "--voice1",
                "joanna",
                "--voice2",
                "hans",
                "-o",
                str(out),
            ],
        )

        assert result.exit_code == 0
        assert str(out) in result.output

    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_pair_custom_pause(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "pair.mp3"
        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize_pair.return_value = SynthesisResult(
            path=out,
            text="strong | stark",
            provider=AudioProviderId.polly,
            voice="joanna+hans",
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize-pair",
                "strong",
                "stark",
                "--pause",
                "1000",
                "-o",
                str(out),
            ],
        )

        assert result.exit_code == 0
        call_args = mock_instance.synthesize_pair.call_args
        # pause is the 6th positional arg
        assert call_args[0][5] == 1000


class TestSynthesizePairBatchCommand:
    @patch(f"{_CLI}.TTSClient")
    @patch(f"{_CLI}.get_provider")
    def test_pair_batch_basic(
        self, mock_get_provider: MagicMock, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "pairs.json"
        input_file.write_text(json.dumps([["strong", "stark"], ["house", "Haus"]]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        mock_get_provider.return_value = _make_mock_provider()
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize_pair_batch.return_value = [
            SynthesisResult(
                path=out_dir / "a.mp3",
                text="strong | stark",
                provider=AudioProviderId.polly,
                voice="joanna+hans",
            ),
            SynthesisResult(
                path=out_dir / "b.mp3",
                text="house | Haus",
                provider=AudioProviderId.polly,
                voice="joanna+hans",
            ),
        ]

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize-pair-batch",
                str(input_file),
                "-d",
                str(out_dir),
            ],
        )

        assert result.exit_code == 0
        mock_instance.synthesize_pair_batch.assert_called_once()


class TestMainGroup:
    @patch(f"{_CLI}.get_provider")
    def test_help(self, mock_get_provider: MagicMock) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "tts" in result.output

    @patch(f"{_CLI}.get_provider")
    def test_synthesize_help(self, mock_get_provider: MagicMock) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["synthesize", "--help"])
        assert result.exit_code == 0
        assert "voice" in result.output.lower()

    @patch(f"{_CLI}.get_provider")
    def test_verbose_flag(self, mock_get_provider: MagicMock) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["-v", "--help"])
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
            main,
            ["--provider", "polly", "synthesize", "hello", "-o", str(out)],
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
            result = runner.invoke(main, ["doctor"])

        return result

    def test_all_required_pass(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path)
        assert result.exit_code == 0
        assert "✓ Python" in result.output
        assert "✓ Provider: polly" in result.output
        assert "✓ ffmpeg" in result.output
        assert "✓ AWS credentials" in result.output
        assert "✓ AWS Polly" in result.output
        assert "✓ Output directory" in result.output

    def test_ffmpeg_missing_fails(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, ffmpeg_found=False)
        assert result.exit_code == 1
        assert "✗ ffmpeg" in result.output

    def test_aws_credentials_fail(self, tmp_path: Path) -> None:
        result = self._run_doctor(
            tmp_path,
            health_checks=[
                HealthCheck(
                    passed=False,
                    message="AWS credentials: not configured (run `aws configure`)",
                ),
                HealthCheck(passed=True, message="AWS Polly access"),
            ],
        )
        assert result.exit_code == 1
        assert "✗ AWS credentials" in result.output

    def test_polly_access_fail(self, tmp_path: Path) -> None:
        result = self._run_doctor(
            tmp_path,
            health_checks=[
                HealthCheck(
                    passed=True,
                    message="AWS credentials (account: 123456789012)",
                ),
                HealthCheck(passed=False, message="AWS Polly access: access denied"),
            ],
        )
        assert result.exit_code == 1
        assert "✗ AWS Polly" in result.output

    def test_uvx_missing_is_optional(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, uvx_found=False)
        # uvx is optional — should not cause failure
        assert result.exit_code == 0
        assert "○ uvx" in result.output

    def test_config_not_found_is_optional(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, config_exists=False)
        assert result.exit_code == 0
        assert "○ Claude Desktop config" in result.output

    def test_server_registered(self, tmp_path: Path) -> None:
        config_data: dict[str, object] = {
            "mcpServers": {"tts": {"command": "uvx"}},
        }
        result = self._run_doctor(tmp_path, config_exists=True, config_data=config_data)
        assert "✓ Claude Desktop MCP: registered" in result.output

    def test_server_not_registered(self, tmp_path: Path) -> None:
        config_data: dict[str, object] = {"mcpServers": {}}
        result = self._run_doctor(tmp_path, config_exists=True, config_data=config_data)
        assert "○ Claude Desktop MCP: not registered" in result.output

    def test_summary_counts(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path)
        assert "passed" in result.output
        assert "failed" in result.output

    def test_linux_no_keys_no_espeak_warns(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELEVENLABS_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            result = self._run_doctor(
                tmp_path, system_platform="Linux", espeak_found=None
            )
        # espeak check is optional — doctor passes but shows the warning
        assert result.exit_code == 0
        assert "espeak-ng/espeak: not found" in result.output
        assert "sudo apt-get install espeak-ng" in result.output

    def test_linux_no_keys_espeak_ng_found(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELEVENLABS_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            result = self._run_doctor(
                tmp_path, system_platform="Linux", espeak_found="espeak-ng"
            )
        assert result.exit_code == 0
        assert "✓ espeak-ng: /usr/bin/espeak-ng" in result.output

    def test_linux_with_api_key_no_espeak_check(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}, clear=False):
            result = self._run_doctor(tmp_path, system_platform="Linux")
        assert "espeak-ng:" not in result.output

    def test_macos_no_espeak_check(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, system_platform="Darwin")
        assert "espeak-ng:" not in result.output


# ---------------------------------------------------------------------------
# install tests (marketplace)
# ---------------------------------------------------------------------------


class TestInstallCommand:
    @patch(f"{_CLI}.get_provider")
    def test_install_success(self, mock_get_provider: MagicMock) -> None:
        mock_get_provider.return_value = _make_mock_provider()

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ["install"])

        assert result.exit_code == 0
        assert "Restart Claude Code" in result.output

    @patch(f"{_CLI}.get_provider")
    def test_install_no_claude(self, mock_get_provider: MagicMock) -> None:
        mock_get_provider.return_value = _make_mock_provider()

        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(main, ["install"])

        assert result.exit_code != 0


class TestUninstallCommand:
    @patch(f"{_CLI}.get_provider")
    def test_uninstall_success(self, mock_get_provider: MagicMock) -> None:
        mock_get_provider.return_value = _make_mock_provider()

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(main, ["uninstall"])

        assert result.exit_code == 0
        assert "Uninstalled." in result.output

    @patch(f"{_CLI}.get_provider")
    def test_uninstall_no_claude(self, mock_get_provider: MagicMock) -> None:
        mock_get_provider.return_value = _make_mock_provider()

        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(main, ["uninstall"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# install-desktop tests (Claude Desktop MCP registration)
# ---------------------------------------------------------------------------

_UVX = "/usr/local/bin/uvx"


class TestInstallDesktopCommand:
    @patch(f"{_CLI}.get_provider")
    def test_creates_config_from_scratch(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
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
            # Ensure no API keys are set; on macOS, say is auto-detected
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("ELEVENLABS_API_KEY", None)
            result = runner.invoke(
                main,
                ["install-desktop", "--output-dir", str(audio_dir)],
            )

        assert result.exit_code == 0
        assert config_path.exists()

        data = json.loads(config_path.read_text())
        server = data["mcpServers"]["tts"]
        assert server["command"] == _UVX
        assert server["args"] == [
            "--from",
            "punt-vox",
            "vox-server",
        ]
        assert server["env"]["VOX_OUTPUT_DIR"] == str(audio_dir)
        assert server["env"]["TTS_PROVIDER"] == "say"

    @patch(f"{_CLI}.get_provider")
    def test_preserves_other_servers(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
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
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=config_path,
            ),
        ):
            result = runner.invoke(
                main,
                ["install-desktop", "--output-dir", str(tmp_path / "audio")],
            )

        assert result.exit_code == 0
        data = json.loads(config_path.read_text())
        assert "other-server" in data["mcpServers"]
        assert "tts" in data["mcpServers"]

    @patch(f"{_CLI}.get_provider")
    def test_overwrites_existing_entry(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        config_path.parent.mkdir(parents=True)
        existing = {
            "mcpServers": {
                "tts": {"command": "old", "args": ["old"]},
            }
        }
        config_path.write_text(json.dumps(existing))

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=config_path,
            ),
        ):
            result = runner.invoke(
                main,
                ["install-desktop", "--output-dir", str(tmp_path / "audio")],
            )

        assert result.exit_code == 0
        assert "Updated existing" in result.output
        data = json.loads(config_path.read_text())
        server = data["mcpServers"]["tts"]
        assert server["command"] == _UVX

    @patch(f"{_CLI}.get_provider")
    def test_fails_when_uvx_not_found(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=None),
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=config_path,
            ),
        ):
            result = runner.invoke(
                main,
                ["install-desktop", "--output-dir", str(tmp_path / "audio")],
            )

        assert result.exit_code != 0
        assert "uvx not found" in result.output

    @patch(f"{_CLI}.get_provider")
    def test_custom_uvx_path(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"

        runner = CliRunner()
        with patch(
            f"{_CLI}._claude_desktop_config_path",
            return_value=config_path,
        ):
            result = runner.invoke(
                main,
                [
                    "install-desktop",
                    "--output-dir",
                    str(tmp_path / "audio"),
                    "--uvx-path",
                    "/custom/bin/uvx",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(config_path.read_text())
        server = data["mcpServers"]["tts"]
        assert server["command"] == "/custom/bin/uvx"

    @patch(f"{_CLI}.get_provider")
    def test_creates_output_directory(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "nested" / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=config_path,
            ),
        ):
            result = runner.invoke(
                main,
                ["install-desktop", "--output-dir", str(audio_dir)],
            )

        assert result.exit_code == 0
        assert audio_dir.is_dir()

    @patch(f"{_CLI}.get_provider")
    def test_install_defaults_openai_when_key_set(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        """OPENAI_API_KEY in env auto-selects openai (ElevenLabs > OpenAI > Polly)."""
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}, clear=False),
        ):
            os.environ.pop("ELEVENLABS_API_KEY", None)
            result = runner.invoke(
                main, ["install-desktop", "--output-dir", str(audio_dir)]
            )

        assert result.exit_code == 0
        assert "Provider: openai" in result.output

        data = json.loads(config_path.read_text())
        env = data["mcpServers"]["tts"]["env"]
        assert env["TTS_PROVIDER"] == "openai"
        assert env["OPENAI_API_KEY"] == "sk-test-key"

    @patch(f"{_CLI}.get_provider")
    def test_install_explicit_openai_with_key(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        """--provider openai with OPENAI_API_KEY set writes key to config."""
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}, clear=False),
        ):
            result = runner.invoke(
                main,
                [
                    "install-desktop",
                    "--output-dir",
                    str(audio_dir),
                    "--provider",
                    "openai",
                ],
            )

        assert result.exit_code == 0
        assert "Provider: openai" in result.output

        data = json.loads(config_path.read_text())
        env = data["mcpServers"]["tts"]["env"]
        assert env["TTS_PROVIDER"] == "openai"
        assert env["OPENAI_API_KEY"] == "sk-test-key"

    @patch(f"{_CLI}.get_provider")
    def test_install_defaults_say_on_macos(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
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
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch("punt_vox.providers.platform.system", return_value="Darwin"),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("ELEVENLABS_API_KEY", None)
            result = runner.invoke(
                main, ["install-desktop", "--output-dir", str(audio_dir)]
            )

        assert result.exit_code == 0
        assert "Provider: say" in result.output

        data = json.loads(config_path.read_text())
        env = data["mcpServers"]["tts"]["env"]
        assert env["TTS_PROVIDER"] == "say"
        assert "OPENAI_API_KEY" not in env

    @patch(f"{_CLI}.get_provider")
    def test_install_explicit_provider_overrides(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}, clear=False),
        ):
            result = runner.invoke(
                main,
                [
                    "install-desktop",
                    "--output-dir",
                    str(audio_dir),
                    "--provider",
                    "polly",
                ],
            )

        assert result.exit_code == 0
        assert "Provider: polly" in result.output

        data = json.loads(config_path.read_text())
        env = data["mcpServers"]["tts"]["env"]
        assert env["TTS_PROVIDER"] == "polly"
        assert "OPENAI_API_KEY" not in env

    @patch(f"{_CLI}.get_provider")
    def test_install_openai_without_key_fails(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("OPENAI_API_KEY", None)
            result = runner.invoke(
                main,
                [
                    "install-desktop",
                    "--output-dir",
                    str(audio_dir),
                    "--provider",
                    "openai",
                ],
            )

        assert result.exit_code != 0
        assert "OPENAI_API_KEY" in result.output

    @patch(f"{_CLI}.get_provider")
    def test_install_elevenlabs_with_key(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        """--provider elevenlabs with ELEVENLABS_API_KEY set writes key to config."""
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch.dict(os.environ, {"ELEVENLABS_API_KEY": "sk_test_key"}, clear=False),
        ):
            result = runner.invoke(
                main,
                [
                    "install-desktop",
                    "--output-dir",
                    str(audio_dir),
                    "--provider",
                    "elevenlabs",
                ],
            )

        assert result.exit_code == 0
        assert "Provider: elevenlabs" in result.output

        data = json.loads(config_path.read_text())
        env = data["mcpServers"]["tts"]["env"]
        assert env["TTS_PROVIDER"] == "elevenlabs"
        assert env["ELEVENLABS_API_KEY"] == "sk_test_key"

    @patch(f"{_CLI}.get_provider")
    def test_install_elevenlabs_without_key_fails(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("ELEVENLABS_API_KEY", None)
            result = runner.invoke(
                main,
                [
                    "install-desktop",
                    "--output-dir",
                    str(audio_dir),
                    "--provider",
                    "elevenlabs",
                ],
            )

        assert result.exit_code != 0
        assert "ELEVENLABS_API_KEY" in result.output

    @patch(f"{_CLI}.get_provider")
    def test_install_defaults_elevenlabs_when_key_set(
        self, mock_get_provider: MagicMock, tmp_path: Path
    ) -> None:
        """ELEVENLABS_API_KEY in env auto-selects elevenlabs."""
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch.dict(os.environ, {"ELEVENLABS_API_KEY": "sk_test_key"}, clear=False),
        ):
            result = runner.invoke(
                main, ["install-desktop", "--output-dir", str(audio_dir)]
            )

        assert result.exit_code == 0
        assert "Provider: elevenlabs" in result.output

        data = json.loads(config_path.read_text())
        env = data["mcpServers"]["tts"]["env"]
        assert env["TTS_PROVIDER"] == "elevenlabs"
        assert env["ELEVENLABS_API_KEY"] == "sk_test_key"
