---
description: "Set session mood for TTS voice"
argument-hint: "<mood> | auto | off"
allowed-tools: ["mcp__plugin_vox_vox__set_config", "Read"]
---

# /vibe command

Set a mood for all TTS speech this session. You interpret the mood and
translate it into ElevenLabs expressive tags.

## Usage

- `/vibe banging my head against the wall` — set manual vibe
- `/vibe auto` — return to automatic vibe detection (default)
- `/vibe off` — disable vibe entirely
- `/vibe` — show current vibe and mode

## Modes

**auto** (default): Vibe tags update automatically at each task
completion based on session signals (test results, lint, git ops).
You interpret the signals and pick appropriate tags during stop-hook
continuations.

**manual**: User-specified mood overrides auto-detection. The manual
mood takes priority when choosing tags at stop time.

**off**: No vibe tags applied. Voice is neutral.

## Your role: voice director

When the user sets a vibe, you translate it into 1-3 ElevenLabs expressive
tags that capture the mood. This is a creative interpretation — use your
judgment.

ElevenLabs eleven_v3 expressive tags are bracketed text that color delivery
for ~4-5 words. The model understands:

- Emotions: `[frustrated]`, `[excited]`, `[melancholy]`, `[smug]`, `[weary]`
- Actions: `[sighs]`, `[laughs]`, `[whispers]`, `[yawns]`
- Directions: `[dramatic tone]`, `[slow]`, `[rushed]`
- Scenes: `[announcing a winner]`, `[telling a secret]`

Examples of your translation:

| Mood / Signals | Tags you'd write |
|----------------|-----------------|
| `banging my head against the wall` | `[frustrated] [sighs]` |
| `just shipped a release` | `[excited]` |
| `3am and still debugging` | `[tired] [slow]` |
| `presenting to the board` | `[confident] [dramatic tone]` |
| tests-fail, tests-fail, cmd-fail | `[frustrated] [sighs]` |
| tests-pass, tests-pass, git-push-ok | `[excited]` |
| tests-pass after tests-fail | `[relieved]` |

Keep it to 1-3 tags. Fewer is better — let the mood breathe.

## Implementation

Use the `set_config` MCP tool for all writes. Read `.vox/config.md` for
status queries.

- **`/vibe <mood>`**: Interpret the mood, choose tags, then call:
  `set_config(updates={"vibe": "<mood text>", "vibe_tags": "<your tags>", "vibe_mode": "manual"})`
- **`/vibe auto`**: Clear stale manual state and set mode:
  `set_config(updates={"vibe_tags": "", "vibe": "", "vibe_mode": "auto"})`
- **`/vibe off`**: Clear all vibe state:
  `set_config(updates={"vibe_tags": "", "vibe": "", "vibe_mode": "off"})`
- **`/vibe` (no argument)**: Read `.vox/config.md` and report current
  `vibe_mode`, `vibe`, and `vibe_tags`

After changing, confirm: `Vibe: <mood> → <tags> [mode]` or `Vibe off.`
