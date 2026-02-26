"""Tests for punt_tts.installer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from punt_tts.installer import (
    MARKETPLACE_KEY,
    PLUGIN_ID,
    TOOL_PERMISSION_DEV,
    TOOL_PERMISSION_PROD,
    TTS_COMMANDS,
    InstallResult,
    StepResult,
    UninstallResult,
    install,
    uninstall,
)

_MOD = "punt_tts.installer"


class TestRegisterMarketplace:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"
        result = install(marketplace_path=mp_path)

        assert mp_path.exists()
        data = json.loads(mp_path.read_text())
        assert MARKETPLACE_KEY in data
        assert data[MARKETPLACE_KEY]["source"]["repo"] == "punt-labs/claude-plugins"

        marketplace_step = result.steps[0]
        assert marketplace_step.name == "Marketplace"
        assert marketplace_step.passed

    def test_skips_when_already_registered(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"
        mp_path.write_text(json.dumps({MARKETPLACE_KEY: {"existing": True}}))

        result = install(marketplace_path=mp_path)
        marketplace_step = result.steps[0]
        assert marketplace_step.passed
        assert "already" in marketplace_step.message

    def test_preserves_other_marketplaces(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"
        mp_path.write_text(json.dumps({"other-market": {"source": "test"}}))

        install(marketplace_path=mp_path)
        data = json.loads(mp_path.read_text())
        assert "other-market" in data
        assert MARKETPLACE_KEY in data


class TestUnregisterMarketplace:
    def test_removes_marketplace(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"
        mp_path.write_text(json.dumps({MARKETPLACE_KEY: {"source": "test"}}))

        # Isolate from real home directory so _unregister_marketplace doesn't
        # find other punt-labs plugins in the real installed_plugins.json.
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with (
            patch(f"{_MOD}._uninstall_plugin") as mock_uninstall,
            patch(f"{_MOD}.Path.home", return_value=fake_home),
        ):
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(marketplace_path=mp_path)

        data = json.loads(mp_path.read_text())
        assert MARKETPLACE_KEY not in data

        marketplace_step = result.steps[3]
        assert marketplace_step.name == "Marketplace"
        assert marketplace_step.passed
        assert "unregistered" in marketplace_step.message

    def test_keeps_marketplace_when_other_plugins(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"
        mp_path.write_text(json.dumps({MARKETPLACE_KEY: {"source": "test"}}))

        registry_data: dict[str, object] = {
            "plugins": {"biff@punt-labs": {}, PLUGIN_ID: {}}
        }

        with (
            patch(f"{_MOD}._uninstall_plugin") as mock_uninstall,
            patch(f"{_MOD}.Path.home", return_value=tmp_path),
        ):
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            # Create the directory structure the uninstaller expects
            registry_dir = tmp_path / ".claude" / "plugins"
            registry_dir.mkdir(parents=True, exist_ok=True)
            (registry_dir / "installed_plugins.json").write_text(
                json.dumps(registry_data)
            )

            result = uninstall(marketplace_path=mp_path)

        # Marketplace should be kept because biff@punt-labs is still installed
        data = json.loads(mp_path.read_text())
        assert MARKETPLACE_KEY in data

        marketplace_step = result.steps[3]
        assert "kept" in marketplace_step.message

    def test_noop_when_not_registered(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"
        mp_path.write_text(json.dumps({"other-market": {"source": "test"}}))

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(marketplace_path=mp_path)

        marketplace_step = result.steps[3]
        assert "not registered" in marketplace_step.message

    def test_noop_when_no_file(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "nonexistent.json"

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(marketplace_path=mp_path)

        marketplace_step = result.steps[3]
        assert marketplace_step.passed


class TestInstallPlugin:
    def test_success(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with (
            patch(f"{_MOD}.subprocess.run") as mock_run,
            patch(f"{_MOD}.shutil.which", return_value="/usr/local/bin/claude"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = install(marketplace_path=mp_path)

        plugin_step = result.steps[1]
        assert plugin_step.name == "Plugin"
        assert plugin_step.passed
        assert "installed" in plugin_step.message

    def test_already_installed_triggers_update(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with (
            patch(f"{_MOD}.subprocess.run") as mock_run,
            patch(f"{_MOD}.shutil.which", return_value="/usr/local/bin/claude"),
        ):
            # First call: install fails with "already installed"
            # Second call: update succeeds
            mock_run.side_effect = [
                MagicMock(returncode=1, stderr="Plugin already installed"),
                MagicMock(returncode=0, stderr=""),
            ]
            result = install(marketplace_path=mp_path)

        plugin_step = result.steps[1]
        assert plugin_step.passed
        assert plugin_step.message == "updated"

    def test_already_installed_already_up_to_date(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with (
            patch(f"{_MOD}.subprocess.run") as mock_run,
            patch(f"{_MOD}.shutil.which", return_value="/usr/local/bin/claude"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1, stderr="Plugin already installed"),
                MagicMock(returncode=1, stderr="Already up to date"),
            ]
            result = install(marketplace_path=mp_path)

        plugin_step = result.steps[1]
        assert plugin_step.passed
        assert "up to date" in plugin_step.message

    def test_already_installed_update_fails(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with (
            patch(f"{_MOD}.subprocess.run") as mock_run,
            patch(f"{_MOD}.shutil.which", return_value="/usr/local/bin/claude"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1, stderr="Plugin already installed"),
                MagicMock(returncode=1, stderr="network error"),
            ]
            result = install(marketplace_path=mp_path)

        plugin_step = result.steps[1]
        assert not plugin_step.passed
        assert "update failed" in plugin_step.message

    def test_claude_not_found(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with patch(f"{_MOD}.shutil.which", return_value=None):
            result = install(marketplace_path=mp_path)

        plugin_step = result.steps[1]
        assert not plugin_step.passed
        assert "not found" in plugin_step.message
        assert not result.installed


class TestRemoveCommands:
    def test_removes_existing_commands(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        for name in TTS_COMMANDS:
            (commands_dir / name).write_text("test")

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(
                marketplace_path=tmp_path / "mp.json",
                commands_dir=commands_dir,
            )

        commands_step = result.steps[1]
        assert commands_step.name == "Commands"
        assert commands_step.passed
        assert f"removed {len(TTS_COMMANDS)}" in commands_step.message

        for name in TTS_COMMANDS:
            assert not (commands_dir / name).exists()

    def test_handles_no_commands(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(
                marketplace_path=tmp_path / "mp.json",
                commands_dir=commands_dir,
            )

        commands_step = result.steps[1]
        assert "removed 0" in commands_step.message


class TestRemovePermissions:
    def test_removes_prod_permission(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps({"permissions": {"allow": [TOOL_PERMISSION_PROD, "other_tool"]}})
        )

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(
                marketplace_path=tmp_path / "mp.json",
                settings_path=settings_path,
            )

        permissions_step = result.steps[2]
        assert permissions_step.name == "Permissions"
        assert permissions_step.passed
        assert "removed" in permissions_step.message

        data = json.loads(settings_path.read_text())
        assert TOOL_PERMISSION_PROD not in data["permissions"]["allow"]
        assert "other_tool" in data["permissions"]["allow"]

    def test_removes_dev_permission(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps({"permissions": {"allow": [TOOL_PERMISSION_DEV]}})
        )

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            uninstall(
                marketplace_path=tmp_path / "mp.json",
                settings_path=settings_path,
            )

        data = json.loads(settings_path.read_text())
        assert TOOL_PERMISSION_DEV not in data["permissions"]["allow"]

    def test_noop_when_not_present(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"permissions": {"allow": ["other_tool"]}}))

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(
                marketplace_path=tmp_path / "mp.json",
                settings_path=settings_path,
            )

        permissions_step = result.steps[2]
        assert "not present" in permissions_step.message

    def test_noop_when_no_settings(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "nonexistent.json"

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "uninstalled")
            result = uninstall(
                marketplace_path=tmp_path / "mp.json",
                settings_path=settings_path,
            )

        permissions_step = result.steps[2]
        assert permissions_step.passed


class TestInstallResult:
    def test_all_pass(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with (
            patch(f"{_MOD}.subprocess.run") as mock_run,
            patch(f"{_MOD}.shutil.which", return_value="/usr/local/bin/claude"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = install(marketplace_path=mp_path)

        assert isinstance(result, InstallResult)
        assert result.installed
        assert all(s.passed for s in result.steps)

    def test_partial_failure(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with patch(f"{_MOD}.shutil.which", return_value=None):
            result = install(marketplace_path=mp_path)

        assert not result.installed
        # Marketplace should pass, plugin should fail
        assert result.steps[0].passed
        assert not result.steps[1].passed


class TestUninstallResult:
    def test_all_pass(self, tmp_path: Path) -> None:
        mp_path = tmp_path / "known_marketplaces.json"

        with patch(f"{_MOD}._uninstall_plugin") as mock_uninstall:
            mock_uninstall.return_value = StepResult("Plugin", True, "not installed")
            result = uninstall(
                marketplace_path=mp_path,
                commands_dir=tmp_path / "commands",
                settings_path=tmp_path / "settings.json",
            )

        assert isinstance(result, UninstallResult)
        assert result.uninstalled
        assert all(s.passed for s in result.steps)
