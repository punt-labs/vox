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
# tools and skills work without a rule update. A global option before the verb
# runs under the broad allow: the ask tier is a safety net, not a security
# boundary, and a ``Bash(vox -*)`` catch-all made ``vox --version`` prompt
# (punt-kit DES-039). The music and rec ``remove`` verbs stay allowed by owner
# decision (PL-1033).
VOX_RULES: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "allow": ("Bash(vox:*)", "mcp__plugin_vox_mic__*", "Skill(vox:*)"),
        "ask": tuple(
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
                "register-guidance",
            )
        ),
    }
)
