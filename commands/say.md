---
description: "Speak text aloud using TTS"
allowed-tools: ["mcp__plugin_tts_tts__synthesize", "mcp__plugin_tts-dev_tts__synthesize"]
---

# /say command

Synthesize the provided text to speech and play it.

## Usage

`/say <text>`

## Implementation

Call the `synthesize` MCP tool with:
- `text`: the user's text argument
- `ephemeral`: `true` (write to `.tts/` in cwd, auto-cleaned)
- `auto_play`: `true`

Do not show the full tool result. Just confirm: "Said: <first 40 chars of text>..."
