---
description: "Control task notifications (hear when tasks finish or need input)"
argument-hint: "y | c | n"
allowed-tools: ["mcp__plugin_tts_vox__set_config", "Read"]
---

# /notify command

Toggle audio notifications for Claude Code events.

## Usage

- `/notify y` — Notify on task completion and permission prompts
- `/notify c` — Continuous: also announce milestones (tests passed, lint clean, code pushed) in real-time
- `/notify n` — Off (default)

## Implementation

Use the `set_config` MCP tool for writes. Read `.tts/config.md` for
status queries.

- **y**: `set_config(key="notify", value="y")`
- **c**: `set_config(key="notify", value="c")`
- **n**: `set_config(key="notify", value="n")`
- **no argument**: Read `.tts/config.md` and report current state

After changing state, confirm with a brief message:

- `y`: "Notifications on. You'll hear when tasks finish or need approval."
- `c`: "Continuous notifications on. You'll also hear real-time milestones."
- `n`: "Notifications off."
