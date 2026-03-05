---
description: "Enable or disable notifications"
argument-hint: "on | off"
allowed-tools: ["mcp__plugin_vox_mic__who", "Edit", "Read"]
---

# /vox command

Toggle task-completion and permission-prompt notifications.

## Usage

- `/vox on` — enable notifications, show voice roster
- `/vox off` — disable notifications

## Implementation

Parse `$ARGUMENTS`:

### `on`

1. Write `notify: "y"` to `.vox/config.md` frontmatter via Edit tool.
2. Call the `who` MCP tool to list voices.
3. Display featured voices with blurbs. Tell user they can pick with `/unmute @<name>`.
4. Confirm: "Notifications on. You'll hear when tasks finish or need approval."

### `off`

1. Write `notify: "n"` to `.vox/config.md` frontmatter via Edit tool.
2. Confirm: "Notifications off."

### No argument or unrecognized

Tell user: "Usage: `/vox on` or `/vox off`"
