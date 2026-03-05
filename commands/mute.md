---
description: "Disable all notifications"
allowed-tools: ["Edit", "Read", "Write"]
---

# /mute command

Disable all audio notifications — no voice, no chimes.

## Config file

The config file is `<root>/.vox/config.md`. To find `<root>`: in a git repo, run `realpath "$(git rev-parse --git-common-dir)/.."` (returns the main repo root, even from worktrees). Outside git, use the current working directory. If the file does not exist, create the `.vox/` directory and the file with YAML frontmatter delimiters (`---`). Never search other directories.

## Implementation

Write `notify: "n"` and `speak: "n"` to `.vox/config.md` frontmatter. Confirm: "Muted."
