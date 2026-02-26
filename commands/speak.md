---
description: "Toggle spoken notifications vs chime-only"
argument-hint: "y | n"
allowed-tools: ["Read", "Write", "Edit"]
---

# /speak command

Toggle whether notifications use spoken words or an audio chime.

## Usage

- `/speak y` — Notifications are spoken (default)
- `/speak n` — Notifications are a chime — no words

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

- **y**: Write the file with `speak: "y"` (preserve other fields)
- **n**: Write the file with `speak: "n"` (preserve other fields)
- **no argument**: Read the file and report current state

If the file doesn't exist, create it with defaults: `voice_enabled: false`, `notify: "n"`, `speak: "y"`.

After changing state, confirm with a brief message:

- `y`: "Spoken notifications enabled."
- `n`: "Chime-only notifications. No words."
