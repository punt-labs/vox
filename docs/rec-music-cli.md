# One verb vocabulary for vox's two audio stores

Design for `vox rec` and `vox music`: give the recordings store the same
group shape the music catalog already has, so the same verbs mean the same
thing across both daemon-owned stores.

- **Bead:** vox-jei3
- **Status:** design, pending operator ratification
- **Depends on:** DES-049 (daemon audio host, record-store containment),
  vox-1hbo (WireReply audit logging), the audio-programs Z model
  (`docs/audio-programs.tex`)

This is the WHAT and the command/protocol shape. It fixes the CLI surface,
the daemon wire-op contracts, the music-generation semantics, the play/say
reconciliation, the outright removal of the old verbs, the touched-surface
list, and the audio-programs model delta. Module structure beyond the wire
ops is the implementation mission's to decide.

## Problem

vox has two daemon-owned audio stores.

- **Recordings** — flat MP3s under `~/.punt-labs/vox/recordings/`, reached by
  three scattered top-level verbs: `vox record`, `vox play`, `vox fetch`.
- **Music albums** — directories of generated tracks, reached by a coherent
  `vox music` Typer group: `list`, `play`, `next`, `status`.

The recordings verbs are the problem the music group already solved:

1. **Scattered.** Three top-level commands instead of one group.
2. **Gaps.** No `list`, no `remove`. You cannot see what you recorded or
   delete it.
3. **Overloaded `play`.** `vox play <arg>` means *either* a store id (daemon
   plays on the host) *or* a local file path (client plays here) — one verb,
   two dispatch rules, decided by a filesystem probe.
4. **A leaked path.** `vox record` prints a daemon-side store path when it can
   prove the file is local, and a different locator when it cannot. The client
   reasons about daemon paths it is not supposed to know.
5. **A stray flag.** `vox fetch -o <path>` lets the client name the output
   path — the one place the client still dictates a filename.

The music group has none of these. The fix is to give recordings the same
group, and to make **both** groups share one verb vocabulary.

## The unified surface (operator-ratified)

```text
vox rec   new "text"   list   play <id>   get <id>   remove <id>
vox music new "prompt"  list   play <id>   get <id>   remove <id>   next   status
```

One vocabulary. `list` and `remove` are spelled out — not `ls`/`rm`. No `-o`
anywhere. Every id-bearing verb takes a **bare store id**, never a path: the
daemon owns the store and every path decision, and the client addresses a
member of the store by the id the daemon issued.

| Verb | rec | music | What it does |
|------|-----|-------|--------------|
| `new "…"` | yes | yes | Create a store member; print its **bare id**. |
| `list` | yes | yes | List the store's members. |
| `play <id>` | yes | yes | Play member `<id>` on the daemon host. |
| `get <id>` | yes | yes | Copy `<id>` into the current directory under its store name. |
| `remove <id>` | yes | yes | Delete `<id>` from the store. |
| `next` | — | yes | Advance the active music source to another part. |
| `status` | — | yes | Show the active music source's status. |

`rec` has no `next`/`status`: a recording is a single file with no playback
program to advance or report on. Those two verbs are music-only because only
music has a running Program (`docs/audio-programs.tex`).

`music` addresses albums by the id shown in `music list` — the same id `play`
already accepts. A recording's id is its store filename (the content-addressed
`md5(text)[:12].mp3`, or the bare `--name` you supplied); an album's id is the
catalog id `music list` prints.

## Old → new mapping and removals

Forward integration (PY-RF-6): the old verbs are renamed or removed outright.
No aliases, no deprecation shims, no migration hints. vox has no installed base
to migrate.

| Old | New | Note |
|-----|-----|------|
| `vox record "t" [--name N]` | `vox rec new "t" [--name N]` | Moves into the `rec` group. Prints the bare id, not a locator. |
| `vox play <id>` (store ref) | `vox rec play <id>` | Store-recording playback; still runs on the daemon host. |
| `vox play <localfile>` | **removed** | `afplay`/`ffplay` play a local file. Not vox's job. |
| `vox fetch <id> -o <path>` | `vox rec get <id>` | Writes `./<id>` in the current directory. The `-o` flag is deleted. |
| — | `vox rec list` | New. |
| — | `vox rec remove <id>` | New. |
| `vox music list/play/next/status` | unchanged verbs | `play` gains a positional `<id>` (see §"play"). |
| — | `vox music new "prompt"` | New — single-track catalog authoring. |
| — | `vox music get <id>` | New. |
| — | `vox music remove <id>` | New. |

**Deleted from the CLI:** the top-level `record`, `play`, and `fetch` commands;
the `-o`/`--output` option on the old `fetch`; the `_emit_record_locator`
daemon-path probing and its `_atomic_write_bytes` helper (the client no longer
names an output path, so there is nothing to atomically place at a chosen
path — the CWD write is a fresh, refuse-on-collision write, §"get"). The
`--output-dir`/`-d` option is **kept** — it belongs to `install-desktop`, not
to the audio stores.

## Command semantics

### new

`vox rec new "text" [--name N] [synthesis flags]` synthesizes speech and stores
it. `vox music new "prompt"` generates one music track (§"Music generation").
Both print **only the bare store id** on success — one token, no path, no host,
nothing to parse around.

```console
$ vox rec new "the build is green"
a1b2c3d4e5f6.mp3
$ vox music new "warm analog pads, slow, D minor, instrumental, loopable"
trk-7f3a91
```

The id is the daemon's; the client echoes it. This replaces the old `record`
locator entirely — no local-vs-remote branch, no store path, no
`play`/`fetch` hint text. If you want to act on the recording, the id is all
the other verbs need.

### list

`vox rec list` prints the store's recording ids, **one per line**, so the
output pipes into the other verbs:

```console
$ vox rec list
a1b2c3d4e5f6.mp3
greeting.mp3
$ vox rec list | while read id; do vox rec get "$id"; done
```

`--json` emits `[{"id": "…", "bytes": N}, …]` for machine consumers. Human
output stays a bare id list — no size column to break the pipe (composability
over decoration). `vox music list` keeps its existing one-line-per-album human
rendering (id, tags, ready/total parts) and `--json`.

### play

`vox rec play <id>` and `vox music play <id>` resolve `<id>` in the daemon's
store and play it **on the daemon host** (the machine with speakers), through
the serialized `PlaybackQueue`. This is the existing `play` wire op (DES-049)
for recordings and the existing `select`/advance path for music.

There is no local-file play. A local file is played with the operating
system's own tool; see §"play/say reconciliation".

`vox music play` today also resolves a **tag radio** (`--style`/`--vibe`/
`--name`, a union of matching albums). The ratified surface names `play <id>`;
preserving the shipped tag radio is flagged as design call D-3.

### get

`vox rec get <id>` copies the recording into the current working directory as a
single file named `<id>`:

```console
$ vox rec get a1b2c3d4e5f6.mp3
./a1b2c3d4e5f6.mp3
```

`vox music get <id>` copies the album into the current working directory as a
**directory named for the album**, containing the album's parts:

```console
$ vox music get trk-7f3a91
./warm-pads--calm/
  part-01.mp3
```

The client never names the target — the store name determines it. The daemon
owns the source name and reports it; the client writes it under the CWD. There
is no `-o`, no path argument, and no local-copy shortcut: even when the store
is on the same filesystem, `get` retrieves through the daemon, because a
same-named local file cannot prove it is the store member (DES-049's identity,
not existence, rule). If the CWD target already exists, `get` errors rather
than clobbering (design call D-1, resolved: error).

### remove

`vox rec remove <id>` deletes the recording from the store. `vox music remove
<id>` deletes the album from the catalog. Both resolve `<id>` under the
daemon-owned root, reject a hostile id before touching the filesystem, and
audit-log a rejection (§"Daemon wire-op contracts"). Removing a music album
that backs the active source is refused (design call D-2, resolved: refuse).

```console
$ vox rec remove a1b2c3d4e5f6.mp3
removed a1b2c3d4e5f6.mp3
```

### next / status (music only)

Unchanged. `vox music next` advances the active source; `vox music status`
reports it. Both already exist in the `music` group.

## CLI decomposition (structure)

This change must **lower** complexity on the files it touches, not relocate
verbs into a bigger `__main__` god-module. The target shape is the one
`cli_music.py` already demonstrates: **a Typer sub-app per noun, backed by a
humble-object command class of thin verb methods that delegate to the engine.**

### The shape

- **One sub-app per store.** `vox rec` is its own Typer group, built by a
  `build_rec_app(formatter)` factory that mirrors `build_music_app` exactly:
  it constructs a `RecCli` humble object and binds each verb method as a
  command (`app.command("new")(cli.new)`, `list`, `play`, `get`, `remove`).
  `__main__.py` gains one line — `app.add_typer(build_rec_app(_formatter),
  name="rec")` — the twin of the existing music line, and **loses** the three
  top-level command bodies (`record`, `play`, `fetch`) and their helpers.
- **`RecCli` is a `@final` humble object**, the twin of `MusicCli`: `__slots__`
  of `(_formatter, _gateway_factory)`, a `_default_gateway` staticmethod, a
  `_fail` staticmethod, and one short method per verb. Each method parses its
  arguments, calls one gateway/engine method, and formats the result through
  the shared `OutputFormatter`. No business logic — the daemon decides.
- **`cli_music.py` grows three focused verb methods** (`new`, `get`, `remove`)
  on the existing `MusicCli`, each the same thin shape. It does not grow a new
  responsibility — `MusicCli` already *is* "the music verbs".

### The paydown

`__main__.py` is the highest-complexity module the change touches, and the
recordings verbs are its worst offenders:

- **`record`** (a ~40-line command body) plus **`_emit_record_locator`** (a
  branch-heavy locator builder that stats the store path, compares byte counts,
  and forks local-vs-remote text) plus **`fetch`** and **`_atomic_write_bytes`**
  plus **`play`** (the overloaded local-file/store-ref dispatch) — all move out
  of `__main__.py`. `_emit_record_locator` and the atomic-write helper are
  **deleted outright**, not relocated: `new` prints a bare id (no locator to
  build) and `get` writes a fresh CWD file that refuses on collision (no
  atomic-replace-at-a-chosen-path to perform). That is negative code — the
  design removes more `__main__` complexity than the `rec` verbs add back,
  because the deleted helpers were the complex part.
- The remaining `__main__.py` unused option aliases (`OutputDirOpt`'s `-o`
  usage on `fetch`, and any `NameOpt`/spec-flag wiring that only `record` used)
  follow their commands out. `NameOpt` and the synthesis-spec flags move onto
  `RecCli.new`, where they are that verb's own parameters, not module-global
  aliases shared with `say`.

The measurable effect: `__main__.py` sheds ~150 lines of command bodies and its
single most branch-heavy function; `radon cc` and the OO ratchet
(`method_ratio`, `module_size`, `max_complexity`) improve on it. `RecCli` lands
as a small, cohesive, fully-methodized class (high `method_ratio`, low
per-method complexity), and `cli_music.py` stays a focused single-class module.
The implementation mission must verify the ratchet improves on `__main__.py`,
not merely holds — the extraction is the paydown, and it names the highest-CC
function it removes (`_emit_record_locator`).

### The engine boundary

`RecCli` delegates through a gateway the way `MusicCli` uses `ProgramGateway` —
a thin protocol with an in-memory implementation for tests (see §"Test
surface"). Recordings today reach the daemon through `VoxClientSync.record`/
`play`/`fetch`; the design adds `rec_list`/`rec_remove` to that client and
routes `RecCli` through a `RecordGateway` protocol (production impl wraps
`VoxClientSync`; test impl is an in-memory store). This keeps the presentation
layer (Typer + `RecCli`) free of transport detail and the dependency arrow
pointing inward (CLI → gateway → client → daemon), per PL-PA-2.

## Daemon wire-op contracts

Every op below obeys the invariants already established in the codebase — do
not reinvent them:

- **Token first.** The router rejects an absent/invalid token before dispatch
  (unchanged).
- **Containment (DES-049 / `RecordStore`).** Any id/ref the client supplies is
  a **bare name**. It is rejected if absolute, separator-bearing (`/`, `\`),
  traversing (`..`), empty, NUL-bearing, or non-printable, *before* any
  filesystem touch; then resolved under the daemon-owned root and verified
  `is_relative_to(root)` **after** `.resolve()`. Record naming and
  play/get/remove refs share one validator.
- **Audit logging (vox-1hbo / `WireReply`).** Every reply is stamped with the
  request id through `WireReply.send`; every rejection is logged at WARNING
  (sanitized) through `WireReply.error`, so a blocked probe is greppable in
  `vox.log`, never silent.

The op names below are illustrative of the wire contract; the implementation
mission owns handler-module layout.

### rec: reuse `record`, `play`, `fetch` unchanged on the wire

- **`rec new`** → the existing `record` op. Request carries `text` and an
  optional bare `name` plus spec fields; replies with the immediate
  `recording` ack and the terminal `audio` frame (`name`, `path`, `bytes`,
  `cached`). **No wire change.** The CLI simply prints `name` (the bare id) and
  discards the `path` — the daemon-path probe is deleted client-side.
- **`rec play`** → the existing `play` op. **No change.**
- **`rec get`** → the existing `fetch` op (single-frame base64 bytes, bounded
  by `FETCH_FRAME_LIMIT_BYTES` = 700 000). **No wire change.** The CLI writes
  the bytes to `./<ref>` instead of to a `-o` path, and refuses on collision.

### rec: `rec_list` — enumerate the recordings store

No id input, so no containment step; there is no hostile name to reject.

- **Request:** `{"type":"rec_list","id":"<hex>"}`
- **Reply:** `{"type":"recordings","id":"<hex>","entries":[{"name":"a1b2c3d4e5f6.mp3","bytes":12345}, …]}`

The daemon lists only the immediate files in its `0700` recordings root
(no recursion, no following out of the root). Order is unspecified; the CLI
prints `name` per line (human) or the full entries (`--json`).

### rec: `rec_remove` — delete one recording

- **Request:** `{"type":"rec_remove","id":"<hex>","ref":"a1b2c3d4e5f6.mp3"}`
- **Reply (ok):** `{"type":"removed","id":"<hex>","name":"a1b2c3d4e5f6.mp3"}`
- **Reply (rejected):** an error frame — a hostile `ref` (audit-logged), or
  `no recording named '<ref>'` when the resolved in-root path is not a file.

`ref` is resolved through the shared validator; the unlink is a single
in-root operation. Removing a non-existent recording is an error, not a silent
success (so a client can trust the result).

### music: `music_new` — generate one track into the catalog

Spends ElevenLabs music credits. Authors **one** track and files it as a fresh
single-track catalog album (§"Music generation"). It does **not** touch the
active Program.

- **Request:** `{"type":"music_new","id":"<hex>","prompt":"<verbatim>","name":"<optional handle>"}`
- **Reply (ack):** `{"type":"generating","id":"<hex>"}` — sent before the
  long generation so the client's response timeout does not fire.
- **Reply (ok):** `{"type":"album","id":"<hex>","album_id":"trk-7f3a91","parts":1}`
- **Reply (rejected):** an error frame — empty prompt (audit-logged), a
  `bad_prompt`/ToS rejection, or a daemon/provider fault. On any rejection the
  catalog is unchanged **and the active Program's mode/pool/`lastError` are
  untouched** — the failure is local to this generate request, reported to the
  caller, never promoted to a program-level `failed`.

`prompt` is the finished ElevenLabs descriptive prompt, used verbatim. There is
no LLM style→prompt expansion in the daemon or the CLI. `name` is an optional
curated handle (matching `music play --name`); absent, the album is addressable
by its generated id alone.

### music: `music_list` — the existing catalog op

Unchanged. `ProgramGateway.catalog()` → the `ProgramSummary` list
(`id`, `style`, `vibe`, `format`, `ready`, `total`, `name`). `vox music list`
already renders it.

### music: `music_get` — copy an album's parts to the client

An album is a directory of parts; a single track is ~3 MB, above the 700 000-byte
`fetch` frame budget. `music_get` therefore delivers a **manifest**, then the
client retrieves each part through the existing single-frame `fetch` op.

- **Request:** `{"type":"music_manifest","id":"<hex>","ref":"trk-7f3a91"}`
- **Reply (ok):** `{"type":"manifest","id":"<hex>","album":"warm-pads--calm","parts":[{"ref":"trk-7f3a91/part-01.mp3","bytes":2950000}, …]}`
- **Reply (rejected):** an error frame — a hostile `ref` (audit-logged) or
  `no album named '<ref>'`.

Containment for the two-level album layout applies the **same discipline
per path segment**: the album id and each part name are each validated as bare
names (reject absolute/separator/traversal/empty/NUL/non-printable), the album
directory is resolved under the music root, and each part is verified
`is_relative_to` the album directory after `.resolve()`. This extends the
vox-dvri validator to a known-depth `<album>/<part>` ref; it does not weaken it.

The client creates `./<album>/` (refusing on collision, D-1) and fetches each
listed part. A part above the frame limit is refused with the same error `rec
get` gives — remote transfer of a realistic (large) album is out of scope for
this cut, inheriting DES-049's chunked-streaming follow-up. Local albums whose
parts fit the frame transfer fully; this keeps `music_get` linear and below the
streaming-transport modeling bar, exactly as `fetch` is.

### music: `music_remove` — delete a catalog album

- **Request:** `{"type":"music_remove","id":"<hex>","ref":"trk-7f3a91"}`
- **Reply (ok):** `{"type":"removed","id":"<hex>","album_id":"trk-7f3a91"}`
- **Reply (rejected):** an error frame — a hostile `ref` (audit-logged),
  `no album named '<ref>'`, or `album trk-7f3a91 is playing; stop it first`
  when the album backs the active Program pool or Radio selection (D-2).

Removing the album deletes its directory and every part within it, in-root.

## Music generation semantics

`vox music new "prompt"` is **catalog authoring**, distinct from the MCP
`music on` flow that authors a running Program's 12-slot pool.

- **Verbatim prompt.** The invoker — human or agent — supplies the finished
  ElevenLabs descriptive prompt. The CLI passes it through unchanged. This
  mirrors the MCP `music` tool contract, where the *calling agent* authors the
  prompt and vox is a pipe to ElevenLabs; the CLI simply removes the agent from
  the loop and lets the invoker author directly.
- **One track per call.** Each `new` generates exactly one track.
- **Where it lands (D-4, resolved).** A **fresh single-track album**,
  addressable by its own generated id. Building a multi-track pool remains the
  MCP agent flow (`music on` with 12 variations); the CLI `new` is for authoring
  individual tracks into the catalog, one at a time.
- **It does not disrupt the active program (D-5, resolved).** `music_new`
  parks the track in the catalog and leaves the running Program — its mode,
  pool, playback, and `lastError` — exactly as it found them. A generation
  failure is reported to the caller and does **not** put the Program into
  `failed`. This is the operator's "park it in the catalog" ruling, and it is
  the crux of the Z-model delta below: `new`/`remove` mutate the **catalog**,
  not the active Program's pool.

## Resolved design calls

- **D-1 — `get` onto an existing CWD target: ERROR, do not clobber.** The
  target name is the store's, not the user's choosing, so a collision is the
  user's to resolve (move away, or run elsewhere). Silent overwrite of a
  differently-sized or different file under a name the user did not pick is a
  data-loss trap. Message: `vox: rec get: ./a1b2c3d4e5f6.mp3 exists`
  (exit 1); for music, the directory target. No `--force` is added now — build
  afresh; add it only if a re-fetch workflow demands it (noted, not built).
- **D-2 — `music remove` of the album backing the active source: REFUSE.** The
  parts are files the active playback reads; deleting them under a live source
  would break it. A clean precondition — "the album's parts do not intersect
  the active Program pool or Radio selection" — is simpler and safer than
  repointing playback. Message: `album <id> is playing; stop it first`.
- **D-6 — `vox play <localfile>`: DROP.** `afplay` (macOS) and `ffplay`
  (Linux) already play a local file; duplicating that inside vox bought only
  the overloaded-`play` dispatch this change removes. Recording playback is
  `vox rec play <id>`; music is `vox music play <id>`; a local file is the OS
  tool's job. (This retires DES-049's live-verify step 6.)
- **play/say reconciliation — KEEP `vox say`.** `say` is the ephemeral
  speak-now verb: synthesize and play immediately, no store, no id. It is not a
  store operation, so it stays a top-level verb and is **not** folded into
  `rec`. The line is clean: `say` is ephemeral; `rec new` is durable (stored,
  returns an id you can `play`/`get`/`remove`). `say` is unchanged.

## Open design calls (leader / operator)

- **D-3 — does `vox music play` keep its tag radio?** The ratified surface
  names `play <id>`. The shipped `music play` also resolves a union radio by
  `--style`/`--vibe`/`--name` (a per-vibe, cross-genre feature). *Recommend:*
  keep `<id>` as the unified-verb positional primary form **and** preserve the
  tag selection as optional arguments/flags on the same `play` command, so no
  shipped feature is silently dropped. Flagged because it stretches the literal
  `play <id>`.
- **D-7 — MCP `mic` parity for the new verbs.** The MCP surface is agent-facing
  and already exposes `music_list`/`music_play`/`music_next` and the `record`
  tool. *Recommend:* do **not** add `rec_list`/`rec_remove`/`music_new`/`get`/
  `remove` MCP tools now — the CLI is the management surface; agents rarely
  list or prune. The only required MCP change is that the `record` tool's
  result loses the client-path field and returns the bare id, matching `rec
  new`. Flagged for the leader to confirm the lean MCP surface.

## Touched surfaces

The leader authors the doc-set files; the implementation mission owns the code.

**Code (implementation mission):**

- `src/punt_vox/__main__.py` — delete top-level `record`, `play`, `fetch` and
  the `-o` option; delete `_emit_record_locator` and the fetch
  `_atomic_write_bytes`; mount the `rec` group.
- The `rec` command group — a humble object + `build_rec_app`, mirroring
  `cli_music.py`'s `MusicCli`/`build_music_app` shape (module name the
  implementation's choice).
- `src/punt_vox/cli_music.py` — add `new`, `get`, `remove` to the group.
- `src/punt_vox/client.py` / `client_sync.py` — add `rec_list`, `rec_remove`,
  `music_new`, `music_get`, `music_remove`; `record`/`play`/`fetch` stay.
- `src/punt_vox/voxd/` — handlers for `rec_list`, `rec_remove`, `music_new`,
  `music_manifest`, `music_remove`; register them in the handler registry;
  extend the music-store resolver to the per-segment `<album>/<part>` ref.
- `src/punt_vox/server.py` — the `record` MCP tool result drops the client path
  (D-7); other `mic` tools unchanged pending the D-7 ruling.

**Docs (leader):**

- `docs/guide-remote-setup.md` — rewrite the record/play/fetch section to
  `rec new`/`rec play`/`rec get`; drop `-o`.
- `src/punt_vox/assets/global-guidance.md` — the **source** of the generated
  usage guide (`~/.punt-labs/vox/CLAUDE.md` is generated by `guidance.py`;
  never edit the generated copy). Update the verb vocabulary here.
- `README.md` — the CLI verb table.
- `CHANGELOG.md` — Changed (rec group, unified verbs), Removed (`record`,
  `play <localfile>`, `fetch`, `-o`), Added (`rec list`/`remove`, `music new`/
  `get`/`remove`).
- `DESIGN.md` — a new ADR (the unified vocabulary; the play/say split; the
  catalog-vs-pool model distinction).
- `prfaq.tex` — **no change.** This is CLI coherence, not a product-direction
  shift.

**Formal model (jms z-spec mission):**

- `docs/audio-programs.tex` — the Catalog delta below.

## Audio-programs Z-model delta

This specifies the exact model changes; it does **not** edit
`docs/audio-programs.tex`. A follow-on z-spec mission (jms) makes the delta
`fuzz -t` clean **before** any implementation dispatches.

The change introduces a **Catalog** the current model abstracts away. Today the
model (finding #7) hides the catalog behind resolved inputs (`diskPool?`,
`newPool?`, `selection?`): albums are never state. `music new`/`music remove`
mutate exactly that hidden set, so the model must make it explicit — while
preserving finding #7 for the Program operations, which still take a
*resolved* pool, not the Catalog.

### New basic type

```text
[ALBUM]   -- an opaque catalog album identifier: the id `music list` prints
             and `play`/`get`/`remove` address.
```

### New state schema

```text
Catalog
  albums : ALBUM ⇸ ℙ PART
  ────────────────────────
  ∀ a : dom albums • albums(a) ≠ ∅
```

`albums` maps each catalogued album id to its set of ready Parts; the invariant
is "a catalogued album has at least one Part" (an empty album is not an album).
`music new` adds a single-track album; the earlier per-vibe pools and the
`diskPool?`/`selection?` inputs the Program and Radio consume are *resolved
from* this Catalog by the orchestration seam, exactly as finding #7 already
abstracts.

### New operation schemas

**`MusicNew` — author one track into a fresh album (success).** Mutates only
the Catalog; the active Program and Radio are framed unchanged, which is the
model image of "does not disrupt the active program".

```text
MusicNew
  ΔCatalog
  ΞProgram
  ΞRadio
  newId? : ALBUM
  track? : PART
  ─────────────────────────────────
  newId? ∉ dom albums
  albums' = albums ⊕ {newId? ↦ {track?}}
```

**`MusicNewBadPrompt` — permanent generation rejection.** The catalog is
untouched **and**, crucially, the Program does not enter `failed` (contrast
`FirstTrackBadPrompt`, which fails an empty-pool Program). The reason is
reported to the caller, not stored as a program-level error.

```text
MusicNewBadPrompt
  ΞCatalog
  ΞProgram
  ΞRadio
  reason? : REASON
  ─────────────────────────────────
  true
```

**`MusicRemove` — delete a catalog album, refusing the live one (D-2).** The
precondition forbids removing an album whose Parts back the active Program pool
or Radio selection.

```text
MusicRemove
  ΔCatalog
  ΞProgram
  ΞRadio
  victim? : ALBUM
  ─────────────────────────────────
  victim? ∈ dom albums
  albums(victim?) ∩ (Program.pool ∪ Radio.selection) = ∅
  albums' = {victim?} ⩤ albums
```

(The `Program.pool`/`Radio.selection` reference is written here as a property;
the jms mission decides the precise schema inclusion — a combined
Catalog+Program+Radio view, or a derived `liveParts` set — to keep `fuzz`
happy. The intent is fixed: refuse when the victim's Parts intersect the live
source.)

`music_get` generates nothing and mutates no state; it is a pure read over
`albums(victim?)`, modeled (if at all) as `ΞCatalog ∧ ΞProgram ∧ ΞRadio` — it
needs no new schema, mirroring how `Fetch` in `docs/vox-dvri-record-store.tex`
is a pure in-store read.

### Properties the delta must preserve

- **`new` grows only the Catalog.** `MusicNew` frames `ΞProgram ∧ ΞRadio`, so a
  full Program pool (`#pool = poolSize`) stays full and playing; authoring a
  catalog track never touches the running program's twelve slots or its mode.
- **`new` never hard-fails the Program.** No `MusicNew*` schema sets the
  Program's `mode` to `failed` or its `lastError`; the existing invariant
  `mode = failed ⇒ pool = ∅` is never engaged by catalog authoring. Only the
  generate-*into-the-program* operations (`FirstTrackBadPrompt`,
  `RetryExhausted`) reach `failed`, and those are unchanged.
- **`remove` cannot delete a playing album.** The `MusicRemove` precondition
  keeps every member of `Program.pool` and `Radio.selection` present in the
  Catalog, so "playing is ready" and "the Radio selection is playable" survive
  a removal.
- **Catalog authoring is credit-visible; the rest is free.** `MusicNew` is the
  one new operation that spends credits (one track); `MusicRemove` and
  `music_get` spend none — consistent with the model's existing
  "replay/consume generates nothing" property.

## Test surface

Coverage must **rise** and the test count must go **up** — the implementation
mission's success criteria require it (`make coverage`, `pytest --co`). This is
achievable without heavy mocks because every command and every daemon op is
designed as a **humble object over an in-memory implementation** (python.md,
PL-TT-5): the command methods delegate to a gateway protocol, and the daemon
handlers delegate to a `RecordStore`/catalog that runs against a `tmp_path`
root. Unit tests hit real logic in milliseconds; subprocess/E2E tests are
reserved for wire framing.

### Command unit tests (humble object, in-memory gateway)

Construct `RecCli(formatter, gateway_factory=lambda: InMemoryRecordGateway())`
and `MusicCli(formatter, in_memory_program_gateway)`; call the verb method;
assert on the emitted payload and exit. No subprocess, no socket.

| Command | Must cover |
|---------|-----------|
| `rec new` | prints exactly the bare id (no path/host); `--name` passthrough; empty text → clean error, exit 1; a daemon error → one-line error, exit 1. |
| `rec list` | empty store → "no recordings"; N recordings → N ids one per line; `--json` → `[{id,bytes}]`; output pipes (no size column in human mode). |
| `rec play` | delegates the id to the gateway `play`; daemon error → exit 1; playback failure surfaced (not silent success). |
| `rec get` | writes `./<id>` with the fetched bytes; **collision** → error, exit 1, existing file untouched (D-1); daemon/oversize/not-found errors → exit 1, no partial file left. |
| `rec remove` | delegates the id; not-found → error; hostile id → error before any delete. |
| `music new` | verbatim prompt passthrough (no expansion); prints the album id; `bad_prompt`/empty prompt → error, exit 1; the active-program status is unchanged after the call. |
| `music list` | existing coverage stays green (album rendering). |
| `music play` | `<id>` positional resolves an album; (pending D-3) tag args still resolve the radio. |
| `music get` | creates `./<album>/` with its parts; **collision** on the directory → error (D-1); a part-level oversize/not-found → exit 1. |
| `music remove` | deletes an idle album; **refuses** the album backing the active source with the D-2 message, exit 1. |

### Registration / surface tests

- `vox rec` and `vox music` expose exactly `new/list/play/get/remove`
  (+ `next/status` for music); Typer `--help` lists them.
- The top-level `record`, `play`, and `fetch` commands **do not exist** (assert
  the registration is gone — the forward-integration guard).
- No `-o`/`--output` option exists anywhere in the audio-store surface;
  `--output-dir`/`-d` still exists only under `install-desktop`.

### Daemon op unit tests (in-memory store under `tmp_path`)

Each handler runs against a real `RecordStore`/catalog rooted at `tmp_path`
with a fake `WebSocket` capturing frames — the pattern
`tests/voxd/test_*_handler.py` already uses.

| Op | Must cover |
|----|-----------|
| `rec_list` | lists only immediate in-root files, no recursion; empty root → empty `entries`; a sub-directory or symlink is not followed out of the root. |
| `rec_remove` | removes an in-root file, replies `removed`; not-found → error frame; the containment corpus (absolute/traversal/separator/empty/NUL/non-printable) → error frame **and a WireReply WARNING audit line**, store unchanged. |
| `music_new` | success adds a single-track album and replies `album` with `parts:1`; empty prompt → audit-logged error; `bad_prompt` → error **and the Program/catalog otherwise unchanged** (the model's `MusicNewBadPrompt`: no `failed`, no `lastError`); the `generating` ack precedes the terminal frame. |
| `music_manifest` | replies the album name + per-part refs + sizes; the per-segment containment corpus on both `<album>` and `<part>` → error + audit line; unknown album → error. |
| `music_remove` | removes an idle album directory and its parts; refuses when the album's parts intersect the active pool/selection (D-2) → error, directory intact. |

### Reused invariants asserted by name

The containment and audit-logging behaviour is reused (DES-049, vox-1hbo), so
the tests **re-assert it at the new ops**, not just at the old ones:

- `test_rec_remove_absolute_ref_rejected`, `_traversal_`, `_separator_`,
  `_empty_`, `_nul_`, `_nonprintable_` — one per rejection class, each
  checking (a) an error frame, (b) a WireReply WARNING line in the log,
  (c) nothing deleted outside the root.
- `test_music_manifest_ref_escape_rejected` — the two-segment
  `<album>/<part>` extension of the same corpus.
- `test_token_does_not_grant_fs_delete` — the trust-model twin of DES-049's
  write test: no authorized `rec_remove`/`music_remove`, whatever its ref,
  deletes outside the root.

### Property / model-alignment tests

Assert the audio-programs delta by name:

- `test_music_new_leaves_full_pool_playing` — a full, playing Program is
  byte-for-byte unchanged after `music_new` (model: `MusicNew` frames
  `ΞProgram`).
- `test_music_new_bad_prompt_does_not_fail_program` — `bad_prompt` returns an
  error and the Program's `mode`/`lastError` are untouched (model:
  `MusicNewBadPrompt`).
- `test_music_remove_refuses_playing_album` — the D-2 precondition
  (`albums(victim) ∩ (pool ∪ selection) = ∅`).

### Live-verify (operator, by ear)

`make install`; `vox daemon restart`; then: `rec new` → `rec play` on the host;
`rec get` into a temp dir (and a second `get` refuses); `music new "<prompt>"`
→ `music play <id>` → the new track sounds right and the running program keeps
playing; hostile ids rejected end-to-end; `music remove` of the playing album
refused. A log cannot judge audio — confirm each audible step with the
operator.
