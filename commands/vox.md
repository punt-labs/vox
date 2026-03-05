---
description: "Enable or disable vox"
argument-hint: "y | n | c"
allowed-tools: ["mcp__plugin_vox_mic__who", "Edit", "Read", "Write"]
---

# /vox command

Enable or disable vox notifications.

## Usage

- `/vox y` — enable vox (chime notifications on task completion and permission prompts)
- `/vox n` — disable vox (no notifications)
- `/vox c` — continuous mode (spoken summaries on task completion; requires `/unmute` for voice)

## Config file

The config file is `<root>/.vox/config.md`. To find `<root>`: in a git repo, run `realpath "$(git rev-parse --git-common-dir)/.."` (returns the main repo root, even from worktrees). Outside git, use the current working directory. If the file does not exist, create the `.vox/` directory and the file with YAML frontmatter delimiters (`---`). Never search other directories.

## Implementation

Parse `$ARGUMENTS`:

### `y`

1. Read `.vox/config.md`. If the file **does not exist** (first init), write both `notify: "y"` and `speak: "y"`. If the file **already exists**, write only `notify: "y"` — preserve the existing `speak` value.
2. Confirm: "Vox enabled."

### `n`

1. Write `notify: "n"` to `.vox/config.md` frontmatter.
2. Confirm: "Vox disabled."

### `c`

1. Read `.vox/config.md`. If the file **does not exist** (first init), write both `notify: "c"` and `speak: "y"`. If the file **already exists**, write only `notify: "c"` — preserve the existing `speak` value.
2. Call the `who` MCP tool to list voices.
3. Display featured voices with blurbs. Tell user they can pick with `/unmute @<name>`.
4. Confirm: "Continuous mode on. You'll hear spoken summaries when tasks finish."

### No argument or unrecognized

Tell user: "Usage: `/vox y` (enable), `/vox n` (disable), or `/vox c` (continuous spoken summaries)"
