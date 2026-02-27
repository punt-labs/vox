---
description: "Set session mood for TTS voice"
argument-hint: "dramatic | whisper | excited | off"
allowed-tools: ["Read", "Write", "Edit"]
---

# /vibe command

Set a mood for all TTS speech this session. Uses ElevenLabs eleven_v3 audio tags.

## Usage

- `/vibe dramatic` — All speech gets `[dramatic tone]` tag
- `/vibe whisper` — All speech gets `[whisper]` tag
- `/vibe excited` — All speech gets `[excited]` tag
- `/vibe off` — Clear the vibe (normal voice)
- `/vibe` — Show current vibe

Any free-form text works — ElevenLabs interprets it as a performance cue. Examples: `tired`, `sigh`, `laughs`, `sad`, `rushed`, `calm and measured`.

## Implementation

Read `.tts/config.md` to check current state. The file has YAML frontmatter:

```yaml
---
voice_enabled: true
notify: "y"
speak: "y"
vibe: "dramatic tone"
---
```

- **`<tag>`**: Write the file with `vibe: "<tag>"` (preserve other fields)
- **off**: Remove the `vibe` field (or set to empty string)
- **no argument**: Read and report the current vibe. If no vibe is set, say "No vibe set — normal voice."

After changing, confirm: `Vibe set: <tag>` or `Vibe cleared.`
