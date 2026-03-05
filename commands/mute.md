---
description: "Chimes only — disable spoken notifications"
allowed-tools: ["Edit", "Read", "Write"]
---

# /mute command

Disable spoken notifications. Chime sounds still play on task completion and permission prompts.

## Config file

The config file is `.vox/config.md` in the current working directory (project root). If it does not exist, create the `.vox/` directory and the file with YAML frontmatter delimiters (`---`). Never search other directories — the file is always at `.vox/config.md` relative to cwd.

## Implementation

Write `speak: "n"` to `.vox/config.md` frontmatter. Confirm: "Muted — chimes only."
