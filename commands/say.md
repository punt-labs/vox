---
description: "Speak text aloud using TTS"
allowed-tools: ["mcp__plugin_tts_tts__speak", "mcp__plugin_tts-dev_tts__speak"]
---

# /say command

Speak the provided text aloud.

## Usage

`/say <text>`

## Implementation

Call the `speak` MCP tool with:

- `text`: the user's text argument
- `ephemeral`: `true` (write to `.tts/` in cwd, auto-cleaned)
- `auto_play`: `true`

Do not show the full tool result. Just confirm: "Said: <first 40 chars of text>..."
