---
description: "Enable voice mode or set session voice"
argument-hint: "[@voice-name]"
allowed-tools: ["mcp__plugin_vox_mic__speak", "mcp__plugin_vox_mic__who"]
---

# /unmute command

Enable voice mode (spoken notifications). Optionally set a session voice or browse the roster. Does not change the notification level — use `/vox y` or `/vox c` for that.

## Usage

- `/unmute` — Enable voice mode (spoken notifications)
- `/unmute @matilda` — Set session voice to matilda and enable voice
- `/unmute @` — Browse voice roster (who's at the mic?)

## Implementation

- **(no argument)**: Call the `speak` MCP tool with `mode="y"`. No text output — the panel confirms.
- **`@<name>`**: Call the `speak` MCP tool with `mode="y"` and `voice="<name>"`. No text output — the panel confirms.
- **`@`** (bare @): Call the `who` MCP tool to list voices. Display featured voices with blurbs in a casual "who's around" format. Tell the user to pick with `/unmute @<name>`.
