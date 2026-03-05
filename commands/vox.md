---
description: "Enable or disable notifications"
argument-hint: "on | off"
allowed-tools: ["mcp__plugin_vox_mic__who", "Edit", "Read", "Write"]
---

# /vox command

Toggle task-completion and permission-prompt notifications.

## Usage

- `/vox on` — enable notifications, show voice roster
- `/vox off` — disable notifications

## Config file

The config file is `<root>/.vox/config.md`. To find `<root>`: in a git repo, run `realpath "$(git rev-parse --git-common-dir)/.."` (returns the main repo root, even from worktrees). Outside git, use the current working directory. If the file does not exist, create the `.vox/` directory and the file with YAML frontmatter delimiters (`---`). Never search other directories.

## Implementation

Parse `$ARGUMENTS`:

### `on`

1. Write `notify: "y"` to `.vox/config.md` frontmatter.
2. Call the `who` MCP tool to list voices.
3. Display featured voices with blurbs. Tell user they can pick with `/unmute @<name>`.
4. Confirm: "Notifications on. You'll hear when tasks finish or need approval."

### `off`

1. Write `notify: "n"` to `.vox/config.md` frontmatter.
2. Confirm: "Notifications off."

### No argument or unrecognized

Tell user: "Usage: `/vox on` or `/vox off`"
