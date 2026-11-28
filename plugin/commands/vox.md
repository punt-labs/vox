---
description: "Enable or disable vox in this repo"
argument-hint: "enable [local|project] [--refresh] [--clear-opt-out] | disable [local|project] [--opt-out]"
allowed-tools: ["mcp__plugin_vox_mic__enablement"]
---

# /vox command

Turn vox on or off for the current repository. The three mid-session switches
— model, provider, and voice — each live on their own top-level slash
command now (`/vox:model`, `/vox:provider`, `/vox:voice`), so `/vox` is
reserved for enablement. The notification level (task-completion vs
continuous) is a personal preference stored in gitignored `vox.local.md`, set
with `vox notify normal|continuous` or `mic:notify`.

## Usage

- `/vox enable` — turn vox on for you in this checkout (`scope="local"`, gitignored; default)
- `/vox enable project` — turn vox on for everyone in this repo (`scope="project"`, committed)
- `/vox enable --refresh` — refresh the deposited guide for the active scope
- `/vox disable` — turn vox off for you in this checkout (`scope="local"`)
- `/vox disable project` — turn vox off for the repo (`scope="project"`)
- `/vox disable project --opt-out` — commit `.punt-labs/vox/opt-out` so vox stays off

## Implementation

Parse `$ARGUMENTS` into `action` (`enable` or `disable`), `scope` (`local` default,
or `project` if `project` or `--scope project` is given), `refresh` (if `--refresh`),
`opt_out` (if `--opt-out`), and `clear_opt_out` (if `--clear-opt-out`):

### `enable`

Call the `enablement` MCP tool with `action="enable"`, `scope=<scope>`,
`refresh=<refresh>`, `clear_opt_out=<clear_opt_out>`. Read the JSON reply and
confirm in one line, e.g. "vox enabled (`local`) in `<repo>`." If `scope="project"`,
remind the user to commit the working-tree change via a PR (neither surface runs git).

### `disable`

Call the `enablement` MCP tool with `action="disable"`, `scope=<scope>`,
`opt_out=<opt_out>`. Read the JSON reply and confirm in one line, e.g.
"vox disabled (`local`) in `<repo>`." If `shared_guide_loads` is true, mention
that the shared `.punt-labs/vox/CLAUDE.md` still loads in Claude Code. If
`scope="project"`, remind the user to commit the working-tree change via a PR.

### No argument or unrecognized

Tell user: "Usage: `/vox enable [local|project] [--refresh] [--clear-opt-out]` or `/vox disable [local|project] [--opt-out]`. To switch model, provider, or voice mid-session, use `/vox:model`, `/vox:provider`, or `/vox:voice`."
