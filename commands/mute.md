---
description: "Chimes only — disable spoken notifications"
allowed-tools: ["mcp__plugin_vox_mic__speak"]
---

# /mute command

Disable spoken notifications. Chime sounds still play on task completion and permission prompts.

## Implementation

Call the `speak` MCP tool with `mode="n"`. No text output — the panel confirms.
