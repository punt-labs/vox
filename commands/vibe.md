---
description: "Set session mood for TTS voice"
argument-hint: "<mood>"
allowed-tools: ["Read", "Write", "Edit"]
---

# /vibe command

Set a mood for all TTS speech this session. You interpret the mood and
translate it into ElevenLabs expressive tags.

## Usage

- `/vibe banging my head against the wall`
- `/vibe just shipped a release`
- `/vibe 3am and still debugging`
- `/vibe off` — Clear the vibe (normal voice)
- `/vibe` — Show current vibe

## Your role: voice director

When the user sets a vibe, you translate it into 1-3 ElevenLabs expressive
tags that capture the mood. This is a creative interpretation — use your
judgment.

ElevenLabs eleven_v3 expressive tags are bracketed text that color delivery
for ~4-5 words. The model understands:

- Emotions: `[frustrated]`, `[excited]`, `[melancholy]`, `[smug]`
- Actions: `[sighs]`, `[laughs]`, `[whispers]`, `[yawns]`
- Directions: `[dramatic tone]`, `[slow]`, `[rushed]`
- Scenes: `[announcing a winner]`, `[telling a secret]`

Examples of your translation:

| Mood | Tags you'd write |
|------|-----------------|
| `banging my head against the wall` | `[frustrated] [sighs]` |
| `just shipped a release` | `[excited]` |
| `3am and still debugging` | `[tired] [slow]` |
| `presenting to the board` | `[confident] [dramatic tone]` |
| `whisper` | `[whispers]` |

Keep it to 1-3 tags. Fewer is better — let the mood breathe.

## Implementation

Read `.tts/config.md` to check current state. The file has YAML frontmatter:

```yaml
---
voice_enabled: true
notify: "y"
speak: "y"
vibe: "banging my head against the wall"
vibe_tags: "[frustrated] [sighs]"
---
```

- **`<mood>`**: Interpret the mood, choose tags, write both `vibe` and `vibe_tags`
- **off**: Set both `vibe` and `vibe_tags` to empty string
- **no argument**: Read and report the current vibe and its tags

After changing, confirm: `Vibe: <mood> → <tags>` or `Vibe cleared.`
