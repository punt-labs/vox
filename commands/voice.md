---
description: "Control text-to-speech voice mode"
allowed-tools: ["Read", "Write", "Edit"]
---

# /voice command

Control TTS voice mode for this session.

## Usage

- `/voice on` — Enable voice mode (synthesize text responses as audio)
- `/voice off` — Disable voice mode
- `/voice status` — Show current voice mode state

## Implementation

Read the file `$HOME/.claude/tts.local.md` to check current state. The file has YAML frontmatter:

```yaml
---
voice_enabled: true
---
```

- **on**: Write the file with `voice_enabled: true`
- **off**: Write the file with `voice_enabled: false`
- **status**: Read the file and report the current state. If the file doesn't exist, voice mode is off.

After changing state, confirm the action to the user.
