---
description: "Enable voice mode or set session voice"
argument-hint: "[voice-name]"
allowed-tools: ["mcp__plugin_vox_mic__speak", "mcp__plugin_vox_mic__who", "AskUserQuestion"]
---

# /unmute command

Enable voice mode (spoken notifications). Optionally set a session voice or browse the roster. Does not change the notification level — use `/vox y` or `/vox c` for that.

## Usage

- `/unmute` — Enable voice mode (spoken notifications), browse the roster
- `/unmute matilda` — Set session voice to matilda and enable voice

## Implementation

First normalize `$ARGUMENTS`: strip a leading `@` if present (a user may type `@matilda` out of habit) and trim whitespace. Treat a lone `@` as no argument.

- **(no argument)**:
  1. Call the `who` MCP tool to get `current` and `featured` voices.
  2. If `featured` has 2+ voices, build a candidate list: start with `current` (if set), then append `featured` voices, de-duplicating by name. Take the first 4 and call `AskUserQuestion` (label=name, description=blurb; append "(current)" to the current voice's description). Then call `speak` with `mode="y"` and `voice` set to the chosen name. No text output. Stop.
  3. If `featured` has fewer than 2 voices, call `speak` with `mode="y"` (provider default). Done — no text output.
- **`<name>`**: Call the `speak` MCP tool with `mode="y"` and `voice="<name>"`. No text output — the panel confirms.
