---
description: "Enable notifications"
allowed-tools: ["mcp__plugin_vox_mic__who", "Edit", "Read"]
---

# /vox on command

Enable task-completion and permission-prompt notifications. Also shows the voice roster so you can pick a voice.

## Usage

`/vox on`

## Implementation

1. Write `notify: "y"` to `.vox/config.md` frontmatter via Edit tool.
2. Call the `who` MCP tool to list voices.
3. Display featured voices with blurbs. Tell user they can pick with `/unmute @<name>`.
4. Confirm: "Notifications on. You'll hear when tasks finish or need approval."
