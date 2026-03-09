---
description: "Enable or disable vox"
argument-hint: "y | n | c | model <name> | provider <name>"
allowed-tools: ["mcp__plugin_vox_mic__notify", "mcp__plugin_vox_mic__who", "mcp__plugin_vox_mic__unmute"]
---

# /vox command

Enable or disable vox notifications, or switch TTS model/provider mid-session.

## Usage

- `/vox y` — enable vox (notifications on task completion and permission prompts)
- `/vox n` — disable vox (no notifications)
- `/vox c` — continuous mode (notifications on task completion, permission prompts, and real-time signals)
- `/vox model <name>` — switch TTS model (e.g. `v3`, `flash`, `turbo`)
- `/vox provider <name>` — switch TTS provider (e.g. `elevenlabs`, `openai`, `polly`, `say`)

## Implementation

Parse `$ARGUMENTS`:

### `y`

Call the `notify` MCP tool with `mode="y"`. No text output — the panel confirms.

### `n`

Call the `notify` MCP tool with `mode="n"`. No text output — the panel confirms.

### `c`

1. Call the `notify` MCP tool with `mode="c"`.
2. Call the `who` MCP tool to list voices.
3. Display featured voices with blurbs. Tell user they can pick with `/unmute @<name>`.

### `model <name>`

Resolve the model shorthand to full model ID:
- `v3` → `eleven_v3`
- `flash` → `eleven_flash_v2_5`
- `turbo` → `eleven_turbo_v2_5`
- `multilingual` → `eleven_multilingual_v2`
- Anything else → pass through as-is (e.g. `tts-1`, `tts-1-hd`)

Call the `unmute` MCP tool with only the `model` parameter (no text). Confirm: "Switched model to `<full_id>`."

### `provider <name>`

Call the `unmute` MCP tool with only the `provider` parameter (no text). When switching providers, also pass `model` as empty string to clear the previous provider's model from config. Confirm: "Switched provider to `<name>`."

Valid providers: `elevenlabs`, `openai`, `polly`, `say`, `espeak`.

### No argument or unrecognized

Tell user: "Usage: `/vox y|n|c`, `/vox model <name>`, `/vox provider <name>`"
