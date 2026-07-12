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

**auto** (default): Vibe tags update automatically at each task
completion. A bounded window of recent command outcomes drives the mood:
every Bash command exits 0 (`ok`) or non-zero (`fail`), and the mood
deepens with the trailing run of failures. The stop hook resolves the
mood to tags deterministically — no interpretation needed.

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

Auto mode (exit-code driven) resolves the trailing failure run to a fixed
mood: a clean or empty window is `[happy]`; 1-2 failures `[focused]`; 3-4
`[frustrated] [sighs]`; 5+ `[weary]`; the first `ok` after a recent failure
`[relieved]`.

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
