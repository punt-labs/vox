---
description: "Enable voice mode or set session voice"
argument-hint: "[@voice-name]"
allowed-tools: ["mcp__plugin_vox_mic__who", "mcp__plugin_vox_mic__vibe", "Edit", "Read", "Write"]
---

# /unmute command

Enable voice mode (continuous spoken notifications). Optionally set a session voice or browse the roster.

## Usage

- `/unmute` — Enable voice mode (spoken notifications)
- `/unmute @matilda` — Set session voice to matilda and enable voice
- `/unmute @` — Browse voice roster (who's at the mic?)

## Config file

The config file is `<root>/.vox/config.md`. To find `<root>`: in a git repo, run `realpath "$(git rev-parse --git-common-dir)/.."` (returns the main repo root, even from worktrees). Outside git, use the current working directory. If the file does not exist, create the `.vox/` directory and the file with YAML frontmatter delimiters (`---`). Never search other directories.

## Implementation

- **(no argument)**: Write `notify: "c"` and `speak: "y"` to `.vox/config.md` frontmatter. Confirm: "Voice on."
- **`@<name>`**: Write `voice: "<name>"`, `notify: "c"`, and `speak: "y"` to `.vox/config.md` frontmatter. Confirm warmly — voices are people: "matilda's here" not "Session voice set to matilda."
- **`@`** (bare @): Call the `who` MCP tool to list voices. Display featured voices with blurbs in a casual "who's around" format. Tell the user to pick with `/unmute @<name>`.
