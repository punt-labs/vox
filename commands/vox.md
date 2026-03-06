---
description: "Enable or disable vox"
argument-hint: "y | n | c"
allowed-tools: ["mcp__plugin_vox_mic__notify", "mcp__plugin_vox_mic__who"]
---

# /vox command

Enable or disable vox notifications.

## Usage

- `/vox y` — enable vox (chime notifications on task completion and permission prompts)
- `/vox n` — disable vox (no notifications)
- `/vox c` — continuous mode (spoken summaries on task completion; requires `/unmute` for voice)

## Implementation

Parse `$ARGUMENTS`:

### `y`

Call the `notify` MCP tool with `mode="y"`. No text output — the panel confirms.

### `n`

Call the `notify` MCP tool with `mode="n"`. No text output — the panel confirms.

### `c`

1. Call the `notify` MCP tool with `mode="c"`.
2. Call the `who` MCP tool to list voices.
3. Display featured voices with blurbs. Tell user they can pick with `/unmute @<name>`.

### No argument or unrecognized

Tell user: "Usage: `/vox y` (enable), `/vox n` (disable), or `/vox c` (continuous spoken summaries)"
