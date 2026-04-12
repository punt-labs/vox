---
description: "Control background music generation"
argument-hint: "on [--name ...] [style ...] | off | play <name> | list"
allowed-tools: ["mcp__plugin_vox_mic__music", "mcp__plugin_vox_mic__music_play", "mcp__plugin_vox_mic__music_list", "mcp__plugin_vox_mic__status"]
---

# /music command

Control vibe-driven background music generation. When on, vox generates
instrumental tracks derived from the current session vibe and loops them
through voxd at reduced volume. When the vibe changes, a new track
generates to match.

## Usage

- `/music on` -- start music with current vibe
- `/music on style techno` -- start music with a style modifier
- `/music on --name focus-beats` -- generate and save as "focus-beats", or replay if it exists
- `/music off` -- stop music
- `/music play <name>` -- replay a saved track by name
- `/music list` -- show saved tracks with metadata
- `/music` -- show current music state

## Track naming

Tracks are auto-named on generation using a vibe-style-HHMM pattern
(e.g. "happy-techno-1118"). Use `--name` to provide a custom name.
When a saved track with the given name exists, it is replayed without
generation (zero credits).

## Style modifier

The style parameter persists in voxd. `/music on style techno` sets the
style; subsequent `/music on` (without style) reuses it. `/music on
style jazz` changes it.

## Implementation

Parse `$ARGUMENTS`:

### `on` (with optional `--name ...` and `style ...`)

Call the `music` MCP tool with `mode="on"`. If the user provided style
words after `on style`, join them and pass as `style`. If `--name` is
provided, pass as `name`. No text output after changes -- the panel
confirms.

### `off`

Call the `music` MCP tool with `mode="off"`. No text output -- the
panel confirms.

### `play <name>`

Call the `music_play` MCP tool with the track name. No text output --
the panel confirms.

### `list`

Call the `music_list` MCP tool and display the track library.

### No argument

Call the `status` MCP tool and report current music state.

### Requirements

Music generation requires an ElevenLabs paid plan. Each track costs
approximately 2,000 credits (~3 minutes of audio). A typical session
generates 1-3 tracks (one per vibe change).
