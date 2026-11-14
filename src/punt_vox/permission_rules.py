"""The allow rules ``vox enable`` grants in a repo's ``.claude/settings.json``.

Pure data, kept apart from the settings-file mechanics in
:mod:`punt_vox.settings_registration`.
"""

from __future__ import annotations

__all__ = ["VOX_RULES"]

# Least privilege (permissions.md §§ 3, 6): the CLI data plane by name, so
# install/uninstall/enable/disable/daemon/desktop/cache/mcp keep prompting, and
# skills by scoped name (scripts/check-skill-permissions.sh checks _COMMANDS).
_CLI_SUBCOMMANDS: tuple[str, ...] = (
    "call",
    "doctor",
    "log",
    "model",
    "music",
    "notify",
    "provider",
    "rec",
    "say",
    "speak",
    "status",
    "version",
    "vibe",
    "voice",
)
_COMMANDS: tuple[str, ...] = (
    "call",
    "model",
    "music",
    "mute",
    "provider",
    "recap",
    "unmute",
    "vibe",
    "voice",
    "vox",
)

VOX_RULES: tuple[str, ...] = (
    "mcp__plugin_vox_mic__*",
    *(f"Bash(vox {name}:*)" for name in _CLI_SUBCOMMANDS),
    *(f"Skill(vox:{name})" for name in _COMMANDS),
)
