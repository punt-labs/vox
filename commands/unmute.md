---
description: "Enable voice mode or set session voice"
argument-hint: "[@voice-name]"
allowed-tools: ["mcp__plugin_vox_mic__who", "mcp__plugin_vox_mic__vibe", "Edit", "Read"]
---

# /unmute command

Enable voice mode. Optionally set a session voice or browse the roster.

## Usage

- `/unmute` — Enable voice mode (spoken notifications)
- `/unmute @matilda` — Set session voice to matilda and enable voice
- `/unmute @` — Browse voice roster (who's at the mic?)

## Implementation

- **(no argument)**: Write `speak: "y"` to `.vox/config.md` frontmatter via Edit tool. Confirm: "Voice on."
- **`@<name>`**: Write `voice: "<name>"` and `speak: "y"` to `.vox/config.md` frontmatter via Edit tool. Confirm warmly — voices are people: "matilda's here" not "Session voice set to matilda."
- **`@`** (bare @): Call the `who` MCP tool to list voices. Display featured voices with blurbs in a casual "who's around" format. Tell the user to pick with `/unmute @<name>`.
