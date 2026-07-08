"""Tests for punt_vox.desktop_install.DesktopInstaller.

Threat under test (PL-PP-4): the Claude Desktop registration must never
carry a plaintext provider API key. The daemon reads its key from
keys.env at startup, so the server authenticates without the secret ever
touching claude_desktop_config.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from punt_vox.desktop_install import DesktopInstaller
from punt_vox.voxd.config import DaemonConfig

if TYPE_CHECKING:
    import pytest

_MOD = "punt_vox.desktop_install"
_SECRET = "sk-elevenlabs-supersecret-value"


class TestServerEnvNeverCarriesSecret:
    def test_env_excludes_api_key_even_when_exported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", _SECRET)
        env = DesktopInstaller("elevenlabs", tmp_path / "audio").server_env()

        assert "ELEVENLABS_API_KEY" not in env
        assert _SECRET not in "".join(env.values())

    def test_env_is_only_routing_config(self, tmp_path: Path) -> None:
        env = DesktopInstaller("say", tmp_path / "audio").server_env()

        assert env == {
            "TTS_PROVIDER": "say",
            "VOX_OUTPUT_DIR": str(tmp_path / "audio"),
        }


class TestCredentialRequirement:
    def test_key_providers_require_credential(self, tmp_path: Path) -> None:
        assert DesktopInstaller("elevenlabs", tmp_path).requires_credential()
        assert DesktopInstaller("openai", tmp_path).requires_credential()

    def test_keyless_providers_need_no_credential(self, tmp_path: Path) -> None:
        assert not DesktopInstaller("say", tmp_path).requires_credential()
        assert not DesktopInstaller("espeak", tmp_path).requires_credential()


class TestDaemonCanAuthenticate:
    def test_keyless_provider_always_ok(self, tmp_path: Path) -> None:
        assert DesktopInstaller("say", tmp_path).daemon_can_authenticate()

    def test_true_when_key_exported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", _SECRET)
        with patch(f"{_MOD}.keys_env_file", return_value=tmp_path / "absent.env"):
            assert DesktopInstaller("elevenlabs", tmp_path).daemon_can_authenticate()

    def test_true_when_key_in_keys_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The daemon's runtime source (keys.env) satisfies the check."""
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        keys_file = tmp_path / "keys.env"
        keys_file.write_text(f"ELEVENLABS_API_KEY={_SECRET}\n")
        with patch(f"{_MOD}.keys_env_file", return_value=keys_file):
            assert DesktopInstaller("elevenlabs", tmp_path).daemon_can_authenticate()

    def test_false_when_key_absent_everywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        with patch(f"{_MOD}.keys_env_file", return_value=tmp_path / "absent.env"):
            installer = DesktopInstaller("elevenlabs", tmp_path)
            assert not installer.daemon_can_authenticate()

    def test_empty_keys_env_value_is_not_authenticated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("ELEVENLABS_API_KEY=\n")
        with patch(f"{_MOD}.keys_env_file", return_value=keys_file):
            installer = DesktopInstaller("elevenlabs", tmp_path)
            assert not installer.daemon_can_authenticate()


class TestCredentialGuidance:
    def test_names_variable_and_keys_env_without_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", _SECRET)
        keys_file = tmp_path / "keys.env"
        with patch(f"{_MOD}.keys_env_file", return_value=keys_file):
            guidance = DesktopInstaller("elevenlabs", tmp_path).credential_guidance()

        assert "ELEVENLABS_API_KEY" in guidance
        assert str(keys_file) in guidance
        assert "vox daemon install" in guidance
        assert _SECRET not in guidance


class TestDetect:
    def test_explicit_provider_is_lowercased(self, tmp_path: Path) -> None:
        assert DesktopInstaller.detect("ElevenLabs", tmp_path).provider == "elevenlabs"

    def test_none_provider_auto_detects(self, tmp_path: Path) -> None:
        with patch(f"{_MOD}.auto_detect_provider", return_value="say"):
            assert DesktopInstaller.detect(None, tmp_path).provider == "say"


class TestDaemonReadsKeysEnv:
    """End-to-end: the key the installer routes to keys.env is the key the
    daemon loads at startup. Proves the server still authenticates."""

    def test_load_keys_picks_up_elevenlabs_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        keys_file = tmp_path / "keys.env"
        keys_file.write_text(f"ELEVENLABS_API_KEY={_SECRET}\n")

        cfg = DaemonConfig(
            run_dir=tmp_path / "run",
            config_dir=tmp_path,
            log_dir=tmp_path / "logs",
        )
        loaded = cfg.load_keys()

        import os

        assert "ELEVENLABS_API_KEY" in loaded
        assert os.environ["ELEVENLABS_API_KEY"] == _SECRET
