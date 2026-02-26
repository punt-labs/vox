---
description: "Control text-to-speech voice mode"
argument-hint: "on | off | status"
allowed-tools: ["Read", "Write", "Edit"]
---

# /voice command

Control TTS voice mode for this session.

## Usage

- `/voice on` — Enable voice mode (speak text responses as audio)
- `/voice off` — Disable voice mode
- `/voice status` — Show current voice mode state

## Implementation

Read the file `.tts/config.md` to check current state. The file has YAML frontmatter:

```yaml
---
voice_enabled: true
---
```

- **on**: Write the file with `voice_enabled: true`
- **off**: Write the file with `voice_enabled: false`
- **status**: Read the file and report the current state. If the file doesn't exist, voice mode is off.

After changing state, confirm the action to the user.
