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

**You author the prompts.** vox is a pipe to ElevenLabs; it never decides what a
genre sounds like. When you turn music on (and on any style/vibe change) YOU --
the agent, using your own genre knowledge -- write a `base_prompt` plus exactly
12 literal, genre-accurate `variations` (one per pool slot) and pass them to the
`music` tool. voxd generates track `i` from `base_prompt` + `variations[i]`, so
the 12 tracks are 12 distinct tracks *within the genre*. If you pass neither,
voxd falls back to a bare `"<style> music, <mood>. instrumental, loopable."`
prompt -- functional, but flavorless.

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
provided, pass as `name`.

**Author the prompts.** Before calling the tool, write `base_prompt` plus
exactly 12 `variations` for the requested style (see "Authoring prompts" below)
and pass them. Re-author them whenever the style or vibe changes. No text output
after changes -- the panel confirms.

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

## Authoring prompts

You write the descriptions -- Python does not. Follow these rules:

- **Genre-forward and literal.** Lead with the genre and let it dominate. Name
  the real instruments, mode/scale, and forms of the style. "Klezmer, freylekhs
  dance, clarinet and violin, in D freygish" -- not "upbeat happy music".
- **Never name a specific artist, composer, band, or copyrighted work.**
  ElevenLabs Music **rejects** these under its Terms of Service -- the request
  fails with `bad_prompt` and no track is generated. Do NOT write "Chopin
  nocturne", "in the style of Aphex Twin", "Clair de lune", or any named
  person/title. Describe the music *itself* -- form, instruments, mode/scale,
  era, tempo, key, mood -- so the description evokes the same sound without the
  name: "romantic-era solo piano nocturne in E-flat major, lyrical right-hand
  melody over rolling left-hand arpeggios" -- not "Chopin nocturne".
- **Vary WITHIN the genre.** The 12 variations should be 12 distinct tracks of
  the *same* genre. Vary dance form, tempo (BPM), mode/key, lead-instrument
  emphasis, and mood shade. Do NOT drift toward genre-alien instruments or
  production -- a lo-fi Rhodes on a Klezmer pool is the bug.
- **`base_prompt`** is the stem shared by all 12: genre, core instrumentation,
  "instrumental, loopable". End it without trailing punctuation.
- **Each variation** is a short, self-contained clause voxd appends to the base.
- **Never** add generic "background music for deep work / smooth ambient texture
  that cycles / driving beat but not overwhelming / afternoon focus / steady
  working pace" boilerplate. That tail homogenizes every genre into smooth jazz.

### Worked example: `style Klezmer`, vibe "celebratory"

`base_prompt`:

> "Klezmer, traditional Ashkenazi Jewish folk, clarinet and violin lead with
> accordion and upright bass, acoustic, celebratory, instrumental, loopable"

`variations` (exactly 12):

1. "freylekh at 120 BPM in D freygish, clarinet lead, bright and dancing"
2. "bulgar at 132 BPM in G freygish, violin lead, driving hand percussion"
3. "hora at 96 BPM in A minor, accordion lead, lilting triple feel"
4. "doina rubato intro in C freygish, unaccompanied clarinet, mournful then rising"
5. "sher at 116 BPM in D minor, violin and clarinet trading the melody"
6. "khosidl at 88 BPM in G minor, stately accordion, dignified"
7. "terkish at 104 BPM in D freygish, clarinet ornaments over a habanera bass"
8. "freylekh at 140 BPM in E freygish, full ensemble, ecstatic wedding energy"
9. "honga at 100 BPM in A freygish, tsimbl (hammered dulcimer) accents"
10. "nign at 72 BPM in D minor, wordless singing feel on violin, contemplative"
11. "bulgar at 126 BPM in C freygish, clarinet krekhts (sobs), tight and punchy"
12. "kolomeyke at 150 BPM in G major, fiddle-forward, breakneck and joyful"

Every entry is Klezmer; they differ by form, tempo, mode, lead, and mood -- not
by drifting to another genre.

## Requirements

Music generation requires an ElevenLabs paid plan. Each track costs
approximately 2,000 credits (~3 minutes of audio). A typical session
generates 1-3 tracks (one per vibe change).
