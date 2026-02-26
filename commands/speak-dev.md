---
description: "Toggle spoken notifications vs chime-only (dev)"
allowed-tools: ["Read", "Write", "Edit"]
---

# /speak-dev command

Toggle whether notifications use spoken words or an audio chime.

## Usage

- `/speak-dev y` — Notifications are spoken (default)
- `/speak-dev n` — Notifications are a chime — no words

## Implementation

Read the file `$HOME/.claude/tts.local.md` to check current state. The file has YAML frontmatter:

```yaml
---
voice_enabled: false
notify: "n"
speak: "y"
---
```

Based on the argument:

- **y**: Write the file with `speak: "y"` (preserve other fields)
- **n**: Write the file with `speak: "n"` (preserve other fields)
- **no argument**: Read the file and report current state

If the file doesn't exist, create it with defaults: `voice_enabled: false`, `notify: "n"`, `speak: "y"`.

After changing state, confirm with a brief message:
- `y`: "Spoken notifications enabled."
- `n`: "Chime-only notifications. No words."
