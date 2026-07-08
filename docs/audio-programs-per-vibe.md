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
