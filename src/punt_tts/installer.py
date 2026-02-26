"""Marketplace-based plugin installation for punt-tts.

Registers the punt-labs marketplace (if missing), then delegates to
``claude plugin install`` / ``claude plugin uninstall`` for all plugin
lifecycle management.  The SessionStart hook (deployed by the plugin
itself) handles command deployment and permission auto-allow.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Well-known paths ----------------------------------------------------------

MARKETPLACE_PATH = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
COMMANDS_DIR = Path.home() / ".claude" / "commands"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PLUGIN_ID = "tts@punt-labs"
MARKETPLACE_KEY = "punt-labs"
TOOL_PERMISSION_PROD = "mcp__plugin_tts_tts__*"
TOOL_PERMISSION_DEV = "mcp__plugin_tts-dev_tts__*"

# Command files deployed by the SessionStart hook
TTS_COMMANDS = (
    "notify.md",
    "recap.md",
    "say.md",
    "speak.md",
    "voice.md",
)


# Result types --------------------------------------------------------------


@dataclass(frozen=True)
class StepResult:
    """Outcome of a single install/uninstall step."""

    name: str
    passed: bool
    message: str


@dataclass(frozen=True)
class InstallResult:
    """Outcome of a full install attempt."""

    installed: bool
    message: str
    steps: list[StepResult] = field(default_factory=lambda: [])


@dataclass(frozen=True)
class UninstallResult:
    """Outcome of a full uninstall attempt."""

    uninstalled: bool
    message: str
    steps: list[StepResult] = field(default_factory=lambda: [])


# Marketplace registration --------------------------------------------------


def _register_marketplace(
    marketplace_path: Path | None = None,
) -> StepResult:
    """Register the punt-labs marketplace if not already present.

    No CLI command exists for marketplace registration, so we write
    directly to ``known_marketplaces.json``.
    """
    path = marketplace_path or MARKETPLACE_PATH

    marketplaces: dict[str, object] = {}
    if path.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            marketplaces = json.loads(path.read_text())

    if MARKETPLACE_KEY in marketplaces:
        return StepResult("Marketplace", True, "already registered")

    marketplaces[MARKETPLACE_KEY] = {
        "source": {"source": "github", "repo": "punt-labs/claude-plugins"},
        "installLocation": str(
            Path.home() / ".claude" / "plugins" / "marketplaces" / "punt-labs"
        ),
        "autoUpdate": True,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(marketplaces, indent=2) + "\n")
        return StepResult("Marketplace", True, "registered punt-labs")
    except OSError as exc:
        return StepResult("Marketplace", False, f"write failed: {exc}")


def _unregister_marketplace(
    marketplace_path: Path | None = None,
) -> StepResult:
    """Remove the punt-labs marketplace registration.

    Only removes if no other punt-labs plugins remain installed.
    """
    path = marketplace_path or MARKETPLACE_PATH
    if not path.exists():
        return StepResult("Marketplace", True, "no marketplace file")

    try:
        marketplaces = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return StepResult("Marketplace", True, "could not read marketplace file")

    if MARKETPLACE_KEY not in marketplaces:
        return StepResult("Marketplace", True, "not registered")

    # Don't remove marketplace if other punt-labs plugins are installed
    registry_path = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text())
            plugins = registry.get("plugins", {})
            punt_plugins = [
                k for k in plugins if k.endswith("@punt-labs") and k != PLUGIN_ID
            ]
            if punt_plugins:
                return StepResult(
                    "Marketplace",
                    True,
                    f"kept (other punt-labs plugins: {', '.join(punt_plugins)})",
                )
        except (json.JSONDecodeError, OSError):
            pass

    del marketplaces[MARKETPLACE_KEY]
    try:
        _atomic_write(path, json.dumps(marketplaces, indent=2) + "\n")
        return StepResult("Marketplace", True, "unregistered punt-labs")
    except OSError as exc:
        return StepResult("Marketplace", False, f"write failed: {exc}")


# Plugin install/uninstall via CLI ------------------------------------------


def _install_plugin() -> StepResult:
    """Install the tts plugin via ``claude plugin install``."""
    claude = shutil.which("claude")
    if not claude:
        return StepResult("Plugin", False, "claude CLI not found on PATH")

    result = subprocess.run(
        [claude, "plugin", "install", PLUGIN_ID, "--scope", "user"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return StepResult("Plugin", True, "installed")
    stderr = result.stderr.strip()
    if "already" in stderr.lower():
        return StepResult("Plugin", True, "already installed")
    return StepResult("Plugin", False, f"claude plugin install failed: {stderr}")


def _uninstall_plugin() -> StepResult:
    """Uninstall the tts plugin via ``claude plugin uninstall``."""
    claude = shutil.which("claude")
    if not claude:
        return StepResult("Plugin", True, "claude CLI not found (nothing to remove)")

    result = subprocess.run(
        [claude, "plugin", "uninstall", PLUGIN_ID, "--scope", "user"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return StepResult("Plugin", True, "uninstalled")
    stderr = result.stderr.strip()
    if "not found" in stderr.lower() or "not installed" in stderr.lower():
        return StepResult("Plugin", True, "not installed (nothing to remove)")
    return StepResult("Plugin", False, f"claude plugin uninstall failed: {stderr}")


# Cleanup helpers (uninstall only) ------------------------------------------


def _remove_commands(commands_dir: Path | None = None) -> StepResult:
    """Remove tts command files from ``~/.claude/commands/``."""
    target = commands_dir or COMMANDS_DIR
    removed = 0
    for name in TTS_COMMANDS:
        path = target / name
        if path.is_file():
            path.unlink()
            removed += 1
    return StepResult("Commands", True, f"removed {removed}")


def _remove_permissions(settings_path: Path | None = None) -> StepResult:
    """Remove ``mcp__plugin_tts_tts__*`` from permissions.allow."""
    path = settings_path or SETTINGS_PATH
    if not path.exists():
        return StepResult("Permissions", True, "no settings file")

    try:
        settings = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return StepResult("Permissions", True, "could not read settings")

    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return StepResult("Permissions", True, "not present")
    allow: object = permissions.get(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        "allow",
    )
    if not isinstance(allow, list):
        return StepResult("Permissions", True, "not present")

    changed = False
    for perm in (TOOL_PERMISSION_PROD, TOOL_PERMISSION_DEV):
        if perm in allow:
            allow.remove(perm)  # pyright: ignore[reportUnknownMemberType]
            changed = True

    if not changed:
        return StepResult("Permissions", True, "not present")

    try:
        _atomic_write(path, json.dumps(settings, indent=2) + "\n")
        return StepResult("Permissions", True, "removed")
    except OSError as exc:
        return StepResult("Permissions", False, f"write failed: {exc}")


# Helpers -------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write a file atomically via temp file + rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


# Public API -----------------------------------------------------------------


def install(
    marketplace_path: Path | None = None,
) -> InstallResult:
    """Install tts via the punt-labs marketplace.

    Steps:
    1. Register punt-labs marketplace (if missing)
    2. Install plugin via ``claude plugin install``

    The SessionStart hook handles command deployment and permission
    auto-allow on first session.
    """
    steps = [
        _register_marketplace(marketplace_path),
        _install_plugin(),
    ]

    if any(not s.passed for s in steps):
        return InstallResult(
            installed=False,
            message="Installation incomplete (see details above).",
            steps=steps,
        )
    return InstallResult(
        installed=True,
        message="Installed. Restart Claude Code to activate.",
        steps=steps,
    )


def uninstall(
    marketplace_path: Path | None = None,
    commands_dir: Path | None = None,
    settings_path: Path | None = None,
) -> UninstallResult:
    """Uninstall tts plugin and clean up all artifacts.

    Steps:
    1. Uninstall plugin via ``claude plugin uninstall``
    2. Remove deployed commands
    3. Remove MCP tool permissions
    4. Unregister marketplace (if no other punt-labs plugins)
    """
    steps = [
        _uninstall_plugin(),
        _remove_commands(commands_dir),
        _remove_permissions(settings_path),
        _unregister_marketplace(marketplace_path),
    ]

    if any(not s.passed for s in steps):
        return UninstallResult(
            uninstalled=False,
            message="Uninstall incomplete (see details above).",
            steps=steps,
        )
    return UninstallResult(
        uninstalled=True,
        message="Uninstalled.",
        steps=steps,
    )
