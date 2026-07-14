---
description: "Set session mood for TTS voice"
argument-hint: "<mood> | auto | off"
allowed-tools: ["mcp__plugin_vox_mic__vibe", "mcp__plugin_vox_mic__status"]
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

**auto** (default): the agent keeps the vibe current from the
conversation. Every few user prompts a non-blocking reminder nudges the
agent to glance at how the session is going and, if the mood has clearly
shifted, set the vibe — using the same tag translation below. No
deterministic classification: the agent has the whole-session context
(the real success/failure signal) that a per-command hook never did.

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

Keep it to 1-3 tags. Fewer is better — let the mood breathe.

When the auto reminder fires, read the mood the same way and pick tags:
`[happy]` when things are flowing, `[focused]` mid-problem, `[frustrated]
[sighs]` when stuck, `[relieved]` just after a fix, `[weary]` on a long
grind. Skip the update when the mood hasn't changed.

## Implementation

Use the `vibe` MCP tool for all writes. Use the `status` MCP tool for
status queries.

- **`/vibe <mood>`**: Interpret the mood, choose tags, then call:
  `vibe(mood="<mood text>", tags="<your tags>", mode="manual")`
- **`/vibe auto`**: `vibe(tags="", mode="auto")`
- **`/vibe off`**: `vibe(tags="", mode="off")`
- **`/vibe` (no argument)**: Call the `status` MCP tool and report current
  `vibe_mode`, `vibe`, and `vibe_tags`

No text output after changes — the panel confirms with the vibe shift.
