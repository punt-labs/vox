---
description: "Speak text aloud using TTS"
argument-hint: "<text>"
allowed-tools: ["mcp__plugin_tts_vox__speak"]
---

# /say command

Speak the provided text aloud.

## Usage

`/say <text>`

## Implementation

Call the `speak` MCP tool with:

- `text`: the user's text argument
- `ephemeral`: `true` (write to `.vox/` in cwd, auto-cleaned)
- `auto_play`: `true`

Do not show the full tool result. Just confirm: "Said: <first 40 chars of text>..."
