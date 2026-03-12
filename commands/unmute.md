---
description: "Enable voice mode or set session voice"
argument-hint: "[@voice-name]"
allowed-tools: ["mcp__plugin_vox_mic__speak", "mcp__plugin_vox_mic__who", "AskUserQuestion"]
---

# /unmute command

Enable voice mode (spoken notifications). Optionally set a session voice or browse the roster. Does not change the notification level — use `/vox y` or `/vox c` for that.

## Usage

- `/unmute` — Enable voice mode (spoken notifications)
- `/unmute @matilda` — Set session voice to matilda and enable voice
- `/unmute @` — Browse voice roster (who's at the mic?)

## Implementation

- **(no argument)**:
  1. Call the `who` MCP tool to get `current` and `featured` voices.
  2. If `featured` has 2+ voices, call `AskUserQuestion` with up to 4 featured voices as options (label=name, description=blurb). AskUserQuestion has a 4-option max. If there is a `current` voice, put it first with "(current)" appended to its description and fill remaining slots from featured. Then call `speak` with `mode="y"` and `voice` set to the chosen name. The user can also pick "Other" to type any voice name.
  3. If `featured` has fewer than 2 voices, call `speak` with `mode="y"` (provider default). Done — no text output.
- **`@<name>`**: Call the `speak` MCP tool with `mode="y"` and `voice="<name>"`. No text output — the panel confirms.
- **`@`** (bare @): Call the `who` MCP tool to list voices. Display featured voices with blurbs in a casual "who's around" format. Tell the user to pick with `/unmute @<name>`.
