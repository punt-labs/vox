---
description: "Enable voice mode or set session voice"
argument-hint: "[@voice-name]"
allowed-tools: ["mcp__plugin_vox_mic__notify", "mcp__plugin_vox_mic__who"]
---

# /unmute command

Enable voice mode (continuous spoken notifications). Optionally set a session voice or browse the roster.

## Usage

- `/unmute` — Enable voice mode (spoken notifications)
- `/unmute @matilda` — Set session voice to matilda and enable voice
- `/unmute @` — Browse voice roster (who's at the mic?)

## Implementation

- **(no argument)**: Call the `notify` MCP tool with `mode="c"`. No text output — the panel confirms.
- **`@<name>`**: Call the `notify` MCP tool with `mode="c"` and `voice="<name>"`. No text output — the panel confirms.
- **`@`** (bare @): Call the `who` MCP tool to list voices. Display featured voices with blurbs in a casual "who's around" format. Tell the user to pick with `/unmute @<name>`.
