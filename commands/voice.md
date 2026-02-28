---
description: "Control text-to-speech voice mode and session voice"
argument-hint: "on | off | status | <voice-name>"
allowed-tools: ["mcp__plugin_tts_vox__set_config", "Read"]
---

# /voice command

Control TTS voice mode and session voice selection.

## Usage

- `/voice on` — Enable voice mode (speak text responses as audio)
- `/voice off` — Disable voice mode
- `/voice status` — Show current voice mode and session voice
- `/voice <name>` — Set the session voice (e.g. `/voice aria`)
- `/voice clear` — Clear the session voice (revert to provider default)

## Implementation

Use the `set_config` MCP tool for writes. Read `.tts/config.md` for
status queries.

- **on**: `set_config(key="voice_enabled", value="true")`
- **off**: `set_config(key="voice_enabled", value="false")`
- **status**: Read `.tts/config.md` and report voice_enabled state and
  session voice. If the file doesn't exist, voice mode is off and no
  session voice is set.
- **`<name>`**: `set_config(key="voice", value="<name>")` — sets
  the session voice. All subsequent speak/chorus/duet/ensemble calls
  will use this voice as default (unless overridden per-call).
  Common ElevenLabs voices: matilda, aria, roger, charlie, sarah, laura.
- **clear**: `set_config(key="voice", value="")` — clears the session
  voice so calls revert to the provider's default.

After changing state, confirm warmly. Voices are people — say
"alice is here" or "switching to roger", not "Session voice set to
alice. All subsequent speech will use this voice."
