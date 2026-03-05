---
description: "Set notification level"
argument-hint: "y | n | c"
allowed-tools: ["mcp__plugin_vox_mic__who", "Edit", "Read", "Write"]
---

# /vox command

Set the notification level: enabled (chimes), disabled (silent), or continuous (spoken summaries).

## Usage

- `/vox y` — enabled: chime notifications on task completion and permission prompts
- `/vox n` — disabled: no notifications
- `/vox c` — continuous: spoken summaries on task completion (requires `/unmute` for voice)

## Config file

The config file is `<root>/.vox/config.md`. To find `<root>`: in a git repo, run `realpath "$(git rev-parse --git-common-dir)/.."` (returns the main repo root, even from worktrees). Outside git, use the current working directory. If the file does not exist, create the `.vox/` directory and the file with YAML frontmatter delimiters (`---`). Never search other directories.

## Implementation

Parse `$ARGUMENTS`:

### `y`

1. Write `notify: "y"` to `.vox/config.md` frontmatter.
2. Confirm: "Notifications on (chimes)."

### `n`

1. Write `notify: "n"` to `.vox/config.md` frontmatter.
2. Confirm: "Notifications off."

### `c`

1. Write `notify: "c"` to `.vox/config.md` frontmatter.
2. Call the `who` MCP tool to list voices.
3. Display featured voices with blurbs. Tell user they can pick with `/unmute @<name>`.
4. Confirm: "Continuous mode on. You'll hear spoken summaries when tasks finish."

### No argument or unrecognized

Tell user: "Usage: `/vox y` (chimes), `/vox n` (off), or `/vox c` (continuous spoken summaries)"
