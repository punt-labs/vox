---
description: "Toggle spoken notifications vs chime-only"
argument-hint: "y | n"
allowed-tools: ["mcp__plugin_vox_vox__set_config", "Read"]
---

# /speak command

Toggle whether notifications use spoken words or an audio chime.

## Usage

- `/speak y` — Notifications are spoken (default)
- `/speak n` — Notifications are a chime — no words

## Implementation

Use the `set_config` MCP tool for writes. Read `.vox/config.md` for
status queries.

- **y**: `set_config(key="speak", value="y")`
- **n**: `set_config(key="speak", value="n")`
- **no argument**: Read `.vox/config.md` and report current state

After changing state, confirm with a brief message:

- `y`: "Spoken notifications enabled."
- `n`: "Chime-only notifications. No words."
