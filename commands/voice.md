---
description: "Control text-to-speech voice mode and session voice"
argument-hint: "on | off | status | <voice-name>"
allowed-tools: ["mcp__plugin_tts_vox__set_config", "mcp__plugin_tts_vox__list_voices", "Read"]
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

Use the `set_config` MCP tool for writes. Read `.vox/config.md` for
status queries.

- **(no argument)**: Call `list_voices()` MCP tool to see who's around.
  Display the `featured` voices with their blurbs in a casual "who's
  around" format. Show the current voice if set. Tell the user to
  pick with `/voice <name>`. Example output:

  > matilda's on the mic. Here's who else is around:
  >
  > - **aria** — Bright and clear, could narrate your life
  > - **roger** — Steady and reassuring, explains turbulence well
  > - **charlie** — Relaxed and genuine, telling you this over coffee
  > - **laura** — Expressive and warm, brings stories to life
  >
  > Pick one with `/voice <name>` — or any name from the full roster.
- **on**: `set_config(key="voice_enabled", value="true")`
- **off**: `set_config(key="voice_enabled", value="false")`
- **status**: Read `.vox/config.md` and report voice_enabled state and
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
