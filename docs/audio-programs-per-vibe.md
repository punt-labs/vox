# Audio Programs — Albums, Tags & Catalog (vox-q7vh)

**Status:** RATIFIABLE — model + all decisions LOCKED (operator, 2026-07-08). Ready
for the design mission. Author: Claude (COO). Bead: vox-q7vh (direction A). Builds on
the shipped Phase-1 Program model (DES-041, `docs/audio-programs.tex`).

## The reframe (locked)

Per-vibe pools is not a directory-layout problem. The manifest already stores the
pool's identity as data, and voxd keeps no catalog in memory (it re-scans manifests
per `/music list`, and `resolve(name)` reads one manifest *by directory name* — the
coupling to remove). So:

> **manifest = source of truth · catalog = in-memory index over manifests ·
> directory = dumb, collision-proof `<slug>-<id>` storage.**

The by-genre / by-vibe "structure" lives in the catalog as tag queries, never in the
folder tree.

## Album identity: id + tags (LOCKED)

- Every **album** (a Program: up to 12 tracks) has its own **unique id**.
- `style`, `vibe`, and optional `name` are **queryable tags on the album, not a
  key.** An **arbitrary number** of albums is supported, **including many albums that
  share the same `(style, vibe)`**.
- `name`, when set (`--name`), is the album's stable human handle; addressing by name
  resolves to that album regardless of vibe.
- Directory = **`<slug>-<id>/`** — the slug (`<style>--<vibe>` or the curated name) is
  a cosmetic Finder prefix; the short id guarantees uniqueness at arbitrary scale.
  `manifest.json` is authoritative — directory names are never parsed back, so a slug
  collision is harmless.
- Single-segment directory names keep this repo's path-traversal guard
  (`ProgramName` rejects `/`, `..`, dot components) intact.

### Manifest schema (proposed — replaces the old `subject{vibe,style}`)

```jsonc
{
  "id": "a3f1c9",
  "format": "playlist",
  "tags": { "style": "trance", "vibe": "calm", "name": null },
  "created": "2026-07-08T02:14:07Z",
  "parts": [
    { "index": 1,  "file": "001.mp3", "status": "ready", "duration_ms": 182000 },
    // …
    { "index": 12, "file": "012.mp3", "status": "ready", "duration_ms": 190200 }
  ]
}
```

`created` enables "resume the newest matching album."

## Directory listing (the locked layout)

```text
~/Music/vox/
├── trance--calm-a3f1c9/            id=a3f1c9  tags{style:trance, vibe:calm}
│   ├── 001.mp3 … 012.mp3
│   └── manifest.json
├── trance--calm-7b2e04/            id=7b2e04  tags{style:trance, vibe:calm}   ← 2nd, same tags
│   ├── 001.mp3 … 012.mp3
│   └── manifest.json
├── trance--energetic-4c8b11/       id=4c8b11  tags{style:trance, vibe:energetic}
│   └── … manifest.json
├── lofi--calm-1f9a30/              id=1f9a30  tags{style:lofi, vibe:calm}     ← 9/12, still filling
│   ├── 001.mp3 … 009.mp3
│   └── manifest.json
├── klezmer--celebratory-e20d77/    id=e20d77  tags{style:klezmer, vibe:celebratory}
│   └── … manifest.json
├── focus-beats-c40d11/             id=c40d11  tags{name:focus-beats, style:lofi, vibe:focused}
│   └── … manifest.json
└── trance/                         LEGACY (pre-change): no id, manifest.subject.vibe=="trance";
    └── … manifest.json               ignored by tag matching. No migration.
```

## The catalog (in-memory, new)

Built once at startup by parsing every manifest; updated on generation. Replaces the
per-list disk scan. It is the sole object list / play / switch talk to. Queries:

- `by_id(id)`, `by_name(name)` → one album
- `by_tags(style?, vibe?)` → the set of matching albums, `created`-descending
- `newest(style, vibe)` → the most recent matching album (for `/music on` resume)

## Playback: tag-filter → Selection → rotate

Every selector is a tag filter over the catalog producing a **Selection** (an ordered
track list). Single-match = one album; multi-match = a **union radio**.

| Query | Resolves to |
|---|---|
| `play focus-beats` | album `c40d11` (name) |
| `play trance calm` | union `a3f1c9` + `7b2e04` (two albums share the tags) |
| `play trance` | all trance albums |
| `play calm` | all calm albums (cross-genre) |
| `/music on` (no args) | current style + **current session vibe** → matching album (newest if several), generate if none |

Two playback modes:

- **Active Program (generate).** A single album, ≤ 12, vibe-adaptive; `/music on`
  generates + rotates + switches album on a `/vibe` change. **Phase 1, unchanged** —
  only *which* album it binds to is now a catalog lookup.
- **Replay a Selection (consume-only).** Any Selection — name, `(style,vibe)`,
  style-union, vibe-union — shuffle-rotated. No generation, no 12-cap, no vibe-switch.

**`/music on` (LOCKED):** with no parameters, defaults to the current style and the
**current session vibe** — that pair is the selection. It resolves to the album
matching those tags (newest if several exist) and generates a fresh one only if none
matches. An explicit `style …` or `--name …` overrides the corresponding tag.

## The three implementation moves

1. **Record the real vibe as a tag.** `_subject_for` today writes the *style* into
   the vibe field and drops the session vibe — fix it to record `(style, vibe)` tags.
2. **Filter by tags, not resolve-by-name.** `/music on` and the replay surfaces query
   the catalog by tags. *This is the feature.*
3. **Free the directory name** to `<slug>-<id>`; identity is in the manifest.

## No migration

Pre-change directories (no `id`, `subject.vibe == style`) coexist as legacy dirs;
they never match a tag query and are deletable by hand. No converter, no legacy
detector (org no-migration rule).

## Resolved decisions (operator, 2026-07-08)

- **P3 — vibe normalization: NOT handled at the music level.** The album's `vibe`
  tag is the session vibe as-is. `/vibe auto` already constrains the vocabulary
  upstream — `mood.py` buckets into `MOOD_FAMILIES` (bright/dark/neutral) and auto
  mode derives the mood from a fixed signal-trajectory table (~6 outcomes). No
  music-layer canonicalization, and no further constraints for now. If coarser
  bucketing is ever wanted, it belongs to the vibe system, not the album layer.
- **M1 — formal model: model the replay Selection as a no-generation rotation.** It
  is the same shape as the existing Rotate transition with generation disabled, over
  a Selection that may span multiple albums. The active-Program transitions are
  otherwise unchanged (binding an album is a catalog lookup, not a new transition).
  jms includes the Selection rotation in `docs/audio-programs.tex` at design time.

## Next

Spec is ratifiable. Design mission (manifest / catalog / selection decomposition +
write set; jms folds the Selection rotation into the Z model), then implementation,
per the two-phase delegation model.

## Design (decomposition, write set, test plan)

**Author:** Raymond H (rmh), 2026-07-07. Design phase for vox-q7vh. No production
code — this section is the write set the implementation mission builds from.

### The one hard decision, stated first

The reframe is cheap everywhere except one place: **union radio replay can exceed
12 tracks and spans multiple album directories**, and the shipped Phase-1
`ProgramState` is capped at `poolSize` (12) with one active directory. The spec
locks both facts: replay has *no 12-cap*, and the Phase-1 machine is *unchanged*.
Those two locks together forbid shoehorning union replay through `ProgramState`.

So the design splits playback into two sources behind one Protocol:

- **`Program`** (unchanged) — the generate mode: one album, `ProgramState`, ≤ 12,
  filling, vibe-adaptive. Every S1–S16 invariant survives verbatim because the
  entity is not touched.
- **`SelectionPlayback`** (new) — the consume-only replay: a flat shuffle-rotation
  over a `Selection` that may span albums and directories, no cap, no fill, no
  generation.

Both satisfy a new `PlaybackSource` Protocol the loop and channel animate. This is
the central layering change and the one item most needing the leader's review
(risk R1 below). The rejected alternative — relax `ProgramState`'s cap so a
Selection is "just a big Program" — violates the "Phase-1 unchanged" lock and the
`#pool ≤ poolSize` invariant, and would let a 24-track union corrupt every mode
implication (`playingRotating ⟹ #pool = poolSize`). Rejected.

### Layer map (dependency arrow points inward)

```text
identity/value  album_id · album_tags · selection · part · identifiers
      │
domain          manifest · catalog · program (unchanged) · selection_playback
      │            state · invariants · rotate_policy · playback_source (Protocol)
      │
persistence     store (Protocol) · filesystem_store
      │
orchestration   service · control_channel · loop · switch_signal · lifecycle_signal
      │
wire handlers   on/off/next/play/select/list/status handlers · wiring
      │
client/present  program_gateway · client_gateway · program_control
                cli_music · server (music tools)
```

### New modules (the write set — create)

| Module | Type(s) | Responsibility |
|---|---|---|
| `voxd/programs/album_id.py` | `AlbumId` | A short unique hex id (`secrets.token_hex(3)`). Validates, parses, `__eq__`/`__hash__`. `AlbumId.mint(taken)` returns an id absent from a taken set (collision-proof). |
| `voxd/programs/album_tags.py` | `AlbumTags` | `style`, `vibe`, `name: str \| None`. `matches(style?, vibe?) -> bool`, `slug() -> str` (the `<style>--<vibe>` or curated-`name` Finder prefix), wire round-trip. Owns the slug rule (PY-OO-7 — slug is behaviour on the tags, not a free function). |
| `voxd/programs/catalog.py` | `Catalog`, `Album` | `Album` = `(manifest, directory)` binding identity to its on-disk location. `Catalog` = in-memory index built once from `Album`s: `by_id`, `by_name`, `by_tags(style?, vibe?)` newest-first, `newest(style, vibe)`, `add(album)` on generation, `mint_id()`. The sole object list/play/switch consult. |
| `voxd/programs/selection.py` | `Selection`, `SelectedPart` | `SelectedPart` = `(part, directory)` — the per-track directory that union replay needs (a bare `Part` cannot locate its file across albums). `Selection` = ordered `tuple[SelectedPart, ...]` from one or more albums, `from_albums(...)`, `__len__`, `__bool__`. |
| `voxd/programs/selection_playback.py` | `SelectionPlayback` | Consume-only rotation cursor over a `Selection`: `playing`, `last_played`, `rotate()` (shuffle, no immediate repeat, single-track replays), `locate(part) -> Path`. No cap, no fill, no generation. The replay half of `PlaybackSource`. |
| `voxd/programs/playback_source.py` | `PlaybackSource` (Protocol) | The structural interface the loop/channel animate: `playing -> Part \| None`, `rotate() -> None`, `filling -> bool`, `locate(part) -> Path`. `Program` (adapted) and `SelectionPlayback` both satisfy it. |
| `voxd/programs/select_signal.py` | `StartSelection` | The consume-only switch signal (parallel to `SwitchProgram`): retarget the channel to a `SelectionPlayback` over a resolved `Selection`, interrupts, begins at the first track. No fill reconcile arms. |

### Modified modules (the write set — change)

| Module | Change |
|---|---|
| `manifest.py` | Replace `subject: PlaylistSubject` with `id: AlbumId`, `tags: AlbumTags`, `created: datetime`. **Delete `PlaylistSubject` outright** (no shim — PL-PP-1). `to_json`/`from_json` gain `id`/`tags`/`created`; `from_json` raises on a well-formed-but-idless record only when the caller demands an album (legacy handled in the catalog build, not here). Drop the `name: ProgramName` *identity* field: the manifest is now addressed by `id`, and the directory is the store's concern. |
| `identifiers.py` | Keep `ProgramName` but re-document it as **the on-disk directory component only** (`<slug>-<id>`) — the path-traversal guard is exactly what a derived dir name still needs. It is no longer a user-facing identity. `PartRef`, `Reason` unchanged. |
| `store.py` (Protocol) | Replace name-addressed `list_programs`/`resolve`/`open(name)` with: `scan() -> tuple[Album, ...]` (startup catalog build, the only disk walk), `open(directory) -> PartStore`, `create(manifest) -> PartStore` (derives `<slug>-<id>`, stamps `created`). `resolve(name)` is **deleted** — the coupling the spec names. |
| `filesystem_store.py` | Implement the new protocol: `scan` globs `*/manifest.json`, pairs each with its directory into an `Album`, skips idless legacy dirs (debug-logged). `create` composes `tags.slug() + "-" + id` via `ProgramName`, keeping the path guard. `_program_dir` takes a directory, not a name. |
| `service.py` | Hold a `Catalog` built from `store.scan()` at construction. `turn_on` gains `vibe: str \| None`; binds via `_bind_album` = catalog lookup (by name-tag, else `by_tags` newest, else `create` + `catalog.add`). `_subject_for` → `_tags_for(style, vibe, name)` records the *real* session vibe (move #1). New `replay(selection_query)` path builds a `Selection` from `catalog.by_tags`/`by_name`/`by_id` and posts `StartSelection`. `active_directory()` → `locate(part)` delegating to the active `PlaybackSource`. |
| `active_context.py` | `ActiveProgram` unchanged for generate. The player's directory seam widens from `active_directory() -> Path` to `locate(part) -> Path` so union replay resolves per-track; single-album generate returns its one dir for any part. |
| `control_channel.py` | `_program: Program` → `_source: PlaybackSource`; `program`/`retarget` generalise to `source`/`retarget(source)`. Signals' `apply` receives the source. Fill reconcile reads `source.filling` (always `False` for a Selection, so it cancels and idles — no special-casing). |
| `loop.py` | Reads `source.playing` and posts `Rotate`; player resolves the dir via `source.locate(target)`. Otherwise unchanged — the interrupt/natural-end race is source-agnostic. |
| `switch_signal.py` | `SwitchProgram` retargets to a `Program` source (generate). Sibling `StartSelection` (new module) retargets to a `SelectionPlayback`. |
| `on_handler.py` | Parse the new `vibe` wire field; pass to `service.turn_on`. |
| `play_handler.py` → `select_handler.py` | Replace directory-name replay with tag/name/id replay: parse `{style?, vibe?, name?, id?}`, drive `service.replay`. Rename the module to name what it does (select a Selection), delete the old name-addressed handler. |
| `list_handler.py` | Emit tag-rich rows: `id`, `style`, `vibe`, `name`, `ready`, `total`, `created` — from the catalog, not a re-scan. |
| `wiring.py` | Swap `program_play` → `program_select`; register against the catalog-backed service. |
| `program_control.py` | `StartRequest` gains `vibe: str \| None`. New `SelectionRequest(style?, vibe?, name?, id?)`. `ProgramSummary` gains `id`, `style`, `vibe`, `name`. |
| `program_gateway.py` / `client_gateway.py` | `start` carries `vibe`; `play`/`loop` → `select(SelectionRequest)`; `catalog` rows carry tags. |
| `cli_music.py` | `list` renders tags (grouped by style/vibe); `play`/`loop` take `style`/`vibe`/`--name` and query via the gateway; **stop constructing `FilesystemProgramStore` client-side** — the daemon owns the catalog (layering fix, risk R2). |
| `server.py` (`music` tool) | Resolve `vibe = _session.vibe` and pass `StartRequest(style, vibe, name, prompts)`. `music_play` → a select tool taking style/vibe/name. |

### Invariant preservation

**Phase-1 `ProgramState` (S1–S16): untouched.** The generate path still seeds a
`Program` over a single catalog album whose `ready_parts()` is ≤ 12 (a manifest
holds ≤ `poolSize` parts by construction). "Which album" is a *resolution* — a
catalog `by_tags`/`by_name` lookup producing `diskPool?` — exactly the shape of
finding #7's part-index resolution: it happens before the transition, never inside
it. `TurnOn`, `VibeStyleChange`, `FillOk`, `filling ⟹ mode`, `failed ⟹ pool = ∅`,
`playing ∈ pool` all hold because no transition, guard, or field changed.

**New invariants the design adds:**

- **`AlbumId` unique.** `Catalog.mint_id()` loops until the id is absent from
  `by_id`; `create` uses `mkdir(exist_ok=False)` so even a directory race regenerates.
- **A `Selection` never generates.** `SelectionPlayback` has no producer, no fill,
  no `filling=True` path; `PlaybackSource.filling` returns `False` structurally.
- **`playing ∈ selection`.** `SelectionPlayback.rotate()` chooses only from its
  `Selection`; the cursor cannot point outside it (mirrors S4 `playing ⊆ pool`).
- **No immediate repeat in replay** when `#selection ≥ 2`; single-track replays
  (mirrors Rotate's `#pool ≥ 2 ⟹ playing' ≠ playing`).
- **Catalog excludes legacy.** Idless dirs are absent from every query, so a legacy
  `subject.vibe == style` dir can never satisfy `by_tags` (the spec's "never match").

### Catalog build and legacy handling

At service construction, `store.scan()` walks `*/manifest.json` once, pairing each
manifest with its directory. Idless (pre-change) manifests are skipped with a debug
log — no converter, no legacy detector beyond "has an `id`?" (no-migration rule).
The catalog is authoritative thereafter; generation calls `catalog.add(album)` so a
freshly created album is queryable without a re-scan. Legacy dirs are invisible to
vox (Finder-only, deletable by hand) — recommend *not* surfacing them in `list`
(risk R3, a minor decision for the leader).

### `/music on` resolution (the locked default path)

1. `server.music(mode="on")` reads `style` (arg, else persisted) and
   `vibe = _session.vibe` (current session mood), passes both in `StartRequest`.
2. `service.turn_on(style, vibe, name, prompts)`:
   - `name` present → `catalog.by_name(name)` (name overrides vibe).
   - else `catalog.newest(style, vibe)` → the newest matching album if any.
   - none → `catalog.mint_id()`, `store.create` a fresh `(style, vibe, name)`
     album, `catalog.add`, then generate.
3. Seed a `Program` over the bound album's `ready_parts()`; post `SwitchProgram`.
   Full pool rotates from disk; partial resumes filling; empty generates — the
   unchanged activation classification.

### Test plan

Mirror source: one `test_*.py` per new module, extend the touched ones. Cover
happy + invalid + boundary + missing-dependency per PL-TT-3.

- **`album_id`** — mint avoids a taken set; parse rejects non-hex/empty; equality.
- **`album_tags`** — `matches` truth table (style-only, vibe-only, both, neither);
  `slug()` for `(style,vibe)` and curated `name`; slug of names with spaces/slashes
  is filesystem-safe; wire round-trip.
- **`catalog`** — build from mixed manifests; `by_tags` returns **many** albums
  sharing `(style,vibe)` newest-first; `by_name` resolves across vibes; `newest`
  picks the latest `created`; `add` makes a new album queryable; legacy (idless)
  dirs are excluded from every query.
- **`selection` / `selection_playback`** — union spans two directories, each
  `SelectedPart` locates its own file; rotate never repeats with `#selection ≥ 2`;
  single-track replays; `locate` returns the right dir per track; empty selection
  is a caught boundary (no crash — mirrors the empty-pool guard).
- **`manifest`** — round-trip `id`/`tags`/`created`; `from_json` on a legacy record
  (no `id`) is handled at the catalog boundary, not a hard parse crash.
- **`filesystem_store`** — `scan` pairs dirs with manifests and skips legacy;
  `create` derives `<slug>-<id>`, stamps `created`, keeps the path guard; a slug
  collision with a distinct id yields two live dirs.
- **`service`** — `/music on` with a matching album **resumes** (no generation),
  with none **generates**; `--name`/`style` override the tag; the recorded tag is
  the *session vibe*, not the style (move #1 regression test); replay of a
  two-album union plays cap-free with no fill.
- **`playback_source` / `loop`** — the loop plays and rotates a `SelectionPlayback`
  identically to a `Program` (a source-agnostic rotation test); `filling=False`
  keeps the fill reconciler idle under a Selection.
- **wire/CLI/server** — `program_on` carries `vibe`; `program_select` resolves by
  style/vibe/name/id; `list` rows carry tags; `music` tool forwards `_session.vibe`.

### Z-model deltas for jms (`docs/audio-programs.tex`)

The active-Program transitions are **unchanged**. Album binding is a *resolution*,
not a transition — add one sentence to finding #7's neighbourhood: "the pool a
`TurnOn`/`VibeStyleChange` enters (`diskPool?`/`newPool?`) is resolved by a catalog
tag/name lookup before the operation runs, exactly as the 1-based part index is;
the model abstracts the lookup away." No schema change on `Program`.

Add a **consume-only replay** section (per M1 — a no-generation rotation over a
Selection that may span albums):

1. A basic/derived set: `Selection : ℙ PART` (the model keeps `PART` opaque; the
   per-track directory is an implementation resolution, out of scope for Z, like
   the file token behind a `Part`).
2. A minimal `Radio` state schema: `selection : ℙ PART; playing, lastPlayed :
   optional PART`, predicate `playing ⊆ selection ∧ lastPlayed ⊆ selection` and
   `#playing ≤ 1 ∧ #lastPlayed ≤ 1`. **No `poolSize` bound** — this is the delta
   that makes union radio legal (`#selection` is unconstrained).
3. `StartRadio` (`selection? : ℙ PART`, `selection? ≠ ∅`): begins at a member,
   `playing' ∈ selection'`, `selection' = selection?`.
4. `RadioRotate`: the shape of `Rotate` with **generation framing removed** —
   `#selection ≥ 2 ⟹ playing' ∈ selection ∧ #playing' = 1 ∧ playing' ≠ playing`;
   `#selection = 1 ⟹ playing' = playing`; `lastPlayed' = playing`. No `pool`,
   `filling`, `attempts`, `failedParts`, `lastError` — a Radio has none.
5. `RadioNext ≙ RadioRotate` (as `Next ≙ Rotate`), and a Key Property: **"Replay
   generates nothing and is uncapped"** — `RadioRotate`/`StartRadio` touch no
   generation field and impose no `poolSize` bound, so a union of two full albums
   (24 parts) is a legal `Radio` state though never a legal `Program` state.

`fuzz -t` must stay exit 0; recommend `/z-spec:test` (probcli) on the "no immediate
repeat" and "playing ∈ selection" properties for the new schema.

### Open risks / questions for design review

- **R1 (layering — needs a ruling).** The `PlaybackSource` Protocol generalises the
  `ControlChannel`/`ProgramLoop` from `Program` to a source union. This touches the
  proven single-writer concurrency core (vox-73m5, vox-ig52). Recommend accepting
  it: it is the only decomposition that honours *both* locks (no 12-cap on replay,
  Phase-1 unchanged) without duplicating the loop. Alternative (a second parallel
  loop for replay) is worse — two tasks racing one player. **Decision needed.**
- **R2 (CLI direct disk read).** `MusicCli` today builds `FilesystemProgramStore`
  client-side for `list` and part-resolution — a presentation layer reading the
  domain's disk. The catalog is daemon-side and authoritative, so these must become
  gateway calls. Recommend making the move in this change (it is the coupling the
  spec's "catalog is the sole object list talks to" implies). **Confirm the scope.**
- **R3 (legacy visibility).** Recommend legacy idless dirs are invisible to vox
  (excluded from `list`), consistent with "deletable by hand, Finder-only." If the
  operator wants them listed for discoverability, that is a separate legacy-view
  surface. **Minor — confirm the default.**
- **R4 (`created` clock).** `created` is set by the store at `create` time
  (`datetime.now(UTC)`). `newest` ties break by id (stable). Acceptable? No NTP
  dependence; a clock skew only reorders same-second creations. **Confirm.**
- **R5 (`name`-tag vs directory identity).** `--name focus-beats` is a *tag* (slug +
  addressable handle), not a directory key; two albums could in principle carry the
  same `name` tag. Recommend `by_name` returns newest-first like `by_tags` (not a
  uniqueness constraint) — matching the "arbitrary albums" lock. **Confirm no
  name-uniqueness rule is wanted.**
