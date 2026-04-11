---
description: "Control background music generation"
argument-hint: "on [style ...] | off"
allowed-tools: ["mcp__plugin_vox_mic__music", "mcp__plugin_vox_mic__status"]
---

# /music command

Control vibe-driven background music generation. When on, vox generates
instrumental tracks derived from the current session vibe and loops them
through voxd at reduced volume. When the vibe changes, a new track
generates to match.

## Usage

- `/music on` -- start music with current vibe
- `/music on style techno` -- start music with a style modifier
- `/music off` -- stop music
- `/music` -- show current music state

## Style modifier

The style parameter persists in voxd. `/music on style techno` sets the
style; subsequent `/music on` (without style) reuses it. `/music on
style jazz` changes it.

## Implementation

Parse `$ARGUMENTS`:

### `on` (with optional `style ...`)

Call the `music` MCP tool with `mode="on"`. If the user provided style
words after `on style`, join them and pass as `style`. No text output
after changes -- the panel confirms.

### `off`

Call the `music` MCP tool with `mode="off"`. No text output -- the
panel confirms.

### No argument

Call the `status` MCP tool and report current music state.

### Requirements

Music generation requires an ElevenLabs paid plan. Each track costs
approximately 2,000 credits (~3 minutes of audio). A typical session
generates 1-3 tracks (one per vibe change).
