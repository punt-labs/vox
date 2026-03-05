---
description: "Disable all notifications"
allowed-tools: ["Edit", "Read", "Write"]
---

# /mute command

Disable all audio notifications — no voice, no chimes.

## Config file

The config file is `<repo>/.vox/config.md` where `<repo>` is the main repository root: `realpath "$(git rev-parse --git-common-dir)/.."`. This works from both the main repo and worktrees. If the file does not exist, create the `.vox/` directory and the file with YAML frontmatter delimiters (`---`). Never search other directories.

## Implementation

Write `notify: "n"` and `speak: "n"` to `.vox/config.md` frontmatter. Confirm: "Muted."
