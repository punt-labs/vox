"""The permission rules ``vox enable`` grants in a repo's ``.claude/settings.json``.

Pure data, kept apart from the settings-file mechanics in
:mod:`punt_vox.settings_registration`.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["VOX_RULES"]

# Broad allow per surface with exact ask carve-outs (permissions.md § 6): ask
# beats allow, so the verbs that install software, change enablement, manage
# the daemon or clear the cache keep prompting while new safe commands, MCP
# tools and skills work without a rule update. ``Bash(vox -*)`` asks for a
# global option before the verb, which the verb rules would not match. The
# music and rec ``remove`` verbs stay allowed by owner decision (PL-1033).
VOX_RULES: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "allow": ("Bash(vox:*)", "mcp__plugin_vox_mic__*", "Skill(vox:*)"),
        "ask": (
            "Bash(vox -*)",
            *(
                f"Bash(vox {verb}:*)"
                for verb in (
                    "install",
                    "uninstall",
                    "enable",
                    "disable",
                    "daemon",
                    "desktop",
                    "cache",
                    "mcp",
                )
            ),
        ),
    }
)
