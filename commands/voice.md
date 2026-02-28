---
description: "Control text-to-speech voice mode and session voice"
argument-hint: "on | off | status | <voice-name>"
allowed-tools: ["mcp__plugin_tts_vox__set_config", "mcp__plugin_tts_vox__list_voices", "Read", "AskUserQuestion"]
---

# /voice command

Control TTS voice mode and session voice selection.

## Usage

- `/voice` — Browse who's around and pick a voice
- `/voice on` — Enable voice mode (speak text responses as audio)
- `/voice off` — Disable voice mode
- `/voice status` — Show current voice mode and session voice
- `/voice <name>` — Set the session voice (e.g. `/voice aria`)
- `/voice clear` — Clear the session voice (revert to provider default)

## Implementation

Use the `set_config` MCP tool for writes. Read `.tts/config.md` for
status queries.

- **(no argument)**: Call `list_voices()` MCP tool to see who's around.
  Pick 3-4 voices from the `featured` list. Present them via
  `AskUserQuestion` with a "who's around" framing:
  - Question: "Who do you want to hear from?" (header: "Voice")
  - Each option: label = voice name, description = blurb from featured
  - The "Other" option is always available for typing a custom name
  - After the user picks, call `set_config(key="voice", value="<chosen>")`
  - Confirm warmly (e.g. "aria's here — let's go")
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
"aria's here" or "switching to roger", not "Session voice set to
aria. All subsequent speech will use this voice."
