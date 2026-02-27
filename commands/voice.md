---
description: "Control text-to-speech voice mode"
argument-hint: "on | off | status"
allowed-tools: ["mcp__plugin_tts_vox__set_config", "Read"]
---

# /voice command

Control TTS voice mode for this session.

## Usage

- `/voice on` — Enable voice mode (speak text responses as audio)
- `/voice off` — Disable voice mode
- `/voice status` — Show current voice mode state

## Implementation

Use the `set_config` MCP tool for writes. Read `.tts/config.md` for
status queries.

- **on**: `set_config(key="voice_enabled", value="true")`
- **off**: `set_config(key="voice_enabled", value="false")`
- **status**: Read `.tts/config.md` and report the current state. If
  the file doesn't exist, voice mode is off.

After changing state, confirm the action to the user.
