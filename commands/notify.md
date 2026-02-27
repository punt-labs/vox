---
description: "Control task notifications (hear when tasks finish or need input)"
argument-hint: "y | n"
allowed-tools: ["Read", "Write", "Edit"]
---

# /notify command

Toggle audio notifications for Claude Code events.

## Usage

- `/notify y` — Notify on task completion and permission prompts
- `/notify n` — Off (default)

## Implementation

Read the file `.tts/config.md` to check current state. The file has YAML frontmatter:

```yaml
---
voice_enabled: false
notify: "n"
speak: "y"
---
```

Based on the argument:

- **y**: Write the file with `notify: "y"` (preserve other fields)
- **n**: Write the file with `notify: "n"` (preserve other fields)
- **no argument**: Read the file and report current state

If the file doesn't exist, create it with defaults: `voice_enabled: false`, `notify: "n"`, `speak: "y"`.

After changing state, confirm with a brief message:

- `y`: "Notifications on. You'll hear when tasks finish or need approval."
- `n`: "Notifications off."
