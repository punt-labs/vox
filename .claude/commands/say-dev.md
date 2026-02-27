---
description: "[DEV] Speak text aloud using TTS"
argument-hint: "<text>"
allowed-tools: ["mcp__plugin_tts_dev_vox__speak"]
---

# /say-dev command

Speak the provided text aloud (dev plugin).

## Usage

`/say-dev <text>`

## Implementation

Call the `mcp__plugin_tts_dev_vox__speak` MCP tool with:

- `text`: the user's text argument
- `ephemeral`: `true` (write to `.tts/` in cwd, auto-cleaned)
- `auto_play`: `true`

Do not show the full tool result. Just confirm: "Said: <first 40 chars of text>..."
