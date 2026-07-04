---
description: "Control background music generation"
argument-hint: "on [--name ...] [style ...] | off | next | play <name> | list"
allowed-tools: ["mcp__plugin_vox_mic__music", "mcp__plugin_vox_mic__music_play", "mcp__plugin_vox_mic__music_list", "mcp__plugin_vox_mic__music_next", "mcp__plugin_vox_mic__status"]
---

# /music command

Control vibe-driven background music generation. `/music on` plays the first
track as soon as it is ready and then, with no further commands, generates the
rest of the `(vibe, style)` pool (up to 12 distinct tracks) in the background
and **auto-advances** to a different track as each one ends. Once the pool has
12, generation stops and playback **rotates** the pool (shuffled, never the
just-played track) at zero credits. A vibe/style change finishes the current
song, then switches to that vibe's pool (filling it if it has fewer than 12).
Each of the 12 generations uses a distinct musical variation so the pool spans
a range of textures rather than near-identical tracks.

## Usage

- `/music on` -- start music with current vibe
- `/music on style techno` -- start music with a style modifier
- `/music on --name focus-beats` -- generate and save as "focus-beats", or replay if it exists
- `/music off` -- stop music
- `/music next` -- optional manual skip (playback auto-advances on its own): jump to the next track now — rotate the pool if it holds 12+ (zero credits), else generate a fresh one
- `/music play <name>` -- replay a saved track by name
- `/music list` -- show saved tracks with metadata
- `/music` -- show current music state

## Track naming

Tracks are auto-named on generation using a vibe-style-YYYYMMDD-HHMM pattern
(e.g. "happy-techno-20260412-1118"). Use `--name` to provide a custom name.
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

### `next`

Call the `music_next` MCP tool. Triggers regeneration while the current
track keeps playing (gapless). No text output -- the panel confirms.

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
