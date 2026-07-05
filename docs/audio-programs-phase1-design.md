# Audio Programs — Phase 1 Design (playlist format)

**Status:** Design for leader review. Author: Raymond H (`rmh`), 2026-07-05.
**Branch:** `design/audio-programs-phase1`. **Contract:** `docs/audio-programs.tex`
(the verified Z model). **Concept:** `docs/audio-programs-concept.md`.

This design migrates today's music onto a first-class, persisted **Program**
model (playlist format only — no new ElevenLabs engines) and adds **playlist
replay from both CLI and MCP**. Every state, transition, and invariant traces to
a named schema in the `.tex`. Where this doc says "schema `X`", it means the Z
operation or state schema of that name in `docs/audio-programs.tex`.

The design is deliberately whole: it names classes, private state, injected
seams, the manifest, the write-set, the test plan, and the OO debt paydown. It
sketches code where the shape is load-bearing, but writes no `.py`.

---

## 0. Reading guide — model → code map (one table)

| `.tex` schema / symbol | Phase-1 code home |
|---|---|
| `[PART]` basic type | `Part` value object (`programs/part.py`) |
| `[SESSION]` | `SessionId` value object (`programs/identifiers.py`) |
| `[REASON]` (non-empty) | `Reason` value object (validates non-empty) |
| `Format ::= playlist \| podcast \| audiobook` | `Format` enum (`programs/format.py`) |
| `Mode ::= off \| generatingFirst \| ...` | `Mode` enum (`programs/mode.py`) |
| `PartStatus ::= pending \| ... \| failedPart` | `PartStatus` enum (`programs/part.py`) |
| `poolSize : Format ⇸ ℕ₁`, `maxRetry` | `Format.pool_size` / `MAX_RETRY` const |
| `Program` state schema (S-predicate) | `ProgramState` frozen value object — **all invariants validated in `__new__`** (`programs/state.py`) |
| `Init`, `RestartFromDisk` | `ProgramState.initial()` / `ProgramState.restored(...)` classmethods |
| Agent ops (§7): `TurnOn` … `TurnOff` | `Program` generation-path methods (`programs/program.py`) |
| Consume ops (§8): `Rotate`/`Loop`/`TrackEnds`, `Skip`, `PlayPart`, `StartFromDisk` | `Program` consume-path methods + injected `PlaybackPolicy` |
| `filling` flag (orthogonal, finding #2) | `ProgramState._filling: bool`, decoupled from `Mode.playing_filling` |
| `failedParts : PART ⇸ REASON` (finding #5) | `ProgramState._failed_parts: FrozenParts` |
| `lastError` (finding #5) | `ProgramState._last_error: Reason \| None` |
| Part indexing `playlist:2` (finding #7) | `PartRef` + resolution in the CLI/MCP surface, **not** domain state |
| the on-disk pool / manifest | `ProgramManifest` + `FilesystemProgramStore` (`programs/store.py`, `programs/manifest.py`) |
| per-format generation backend | `Producer` protocol + `MusicProducer` (`programs/producer.py`) |

---

## 1. Domain model

The domain is a new package, `src/punt_vox/voxd/programs/`, that replaces
`src/punt_vox/voxd/music/`. The rename is not cosmetic: the model is
format-general, and Phases 2–3 extend it, so the package name must be too. This
is forward integration (PY-RF-6) — the old `music/` package is deleted and every
caller wired to `programs/` in the same PRs; no shims, no re-exports.

### 1.1 The state value object — `ProgramState`

The single highest-value class. It is the Z state schema `Program` made
executable: a `frozen=True, slots=True` value object whose `__new__` validates
**every** invariant in the schema predicate (S1–S16) before returning. Because it
is immutable and self-validating, an illegal state cannot be represented — the
property tests construct states and assert the invariants hold, and every
transition produces a fresh `ProgramState` that re-checks them.

```python
# programs/state.py  (sketch — not final)
@final
class ProgramState:
    """One legal Program state. All Z invariants hold by construction."""
    __slots__ = (
        "_format", "_pool", "_failed_parts", "_playing", "_last_played",
        "_owner", "_mode", "_filling", "_attempts", "_last_error",
    )
    _format: Format
    _pool: frozenset[Part]
    _failed_parts: FrozenParts          # Part ⇸ Reason, disjoint from _pool
    _playing: Part | None               # "optional Part" = at-most-one
    _last_played: Part | None
    _owner: SessionId | None
    _mode: Mode
    _filling: bool
    _attempts: int
    _last_error: Reason | None          # None ⟺ "∅"; absence is the contract

    def __new__(cls, ...) -> Self:
        self = super().__new__(cls)
        # assign, then validate — one guard per S-invariant, e.g.:
        #   S1  len(pool) <= format.pool_size
        #   S5  failed_parts.keys() disjoint from pool
        #   S7  attempts >= 1  <=>  mode is retrying
        #   S8  filling  =>  mode in {generating_first, playing_filling}
        #   S9  filling  =>  owner is not None
        #   S11 mode == off => playing/filling/owner/last_error/failed all empty
        #   ... (S10–S16 mode-shape implications)
        self._check_invariants()        # raises ValueError on any violation
        return self
```

`_last_error: Reason | None` — the `None` is the documented `∅` of the model
(finding: `#lastError ≤ 1`), not a "gave up" sentinel (PY-TS-14 justification
inline). Same for `_playing`, `_last_played`, `_owner`. The at-most-one sets of
the Z model become `T | None`, which is the faithful, precise encoding.

`FrozenParts` is a tiny frozen value object wrapping the `Part ⇸ Reason` finite
map (an immutable mapping with `with_failure(part, reason)` returning a new one)
— not a raw `dict`, per PY-OO-4. It lives beside `Part`.

Transitions never mutate a `ProgramState`; they build a successor. A private
copy-helper keeps each transition to a few lines:

```python
def _evolve(self, **changes: object) -> ProgramState:
    """Return a new state with `changes` applied (re-validates invariants)."""
```

`Init` and `RestartFromDisk` become classmethods `ProgramState.initial()` and
`ProgramState.restored(fmt, disk_pool)` — they map field-for-field to the Z
schemas.

### 1.2 The entity — `Program`

`Program` is the mutable domain entity that owns the *current* `ProgramState`
and exposes one method per Z operation. Each method: checks the operation
precondition (raising `ValueError` on violation — that is the Z guard), computes
the successor `ProgramState` (which re-validates), and stores it. Methods return
`None` (mutators, PY-OP-8) or a small result object where the surface needs one.

```python
# programs/program.py  (sketch)
class Program:
    __slots__ = ("_state", "_policy")
    _state: ProgramState
    _policy: PlaybackPolicy               # injected rotate-vs-sequential seam

    def __new__(cls, state: ProgramState, policy: PlaybackPolicy) -> Self: ...

    # --- generation path (§7) --------------------------------------------
    def turn_on(self, who: SessionId) -> None: ...          # TurnOn
    def first_track_ok(self, new: Part) -> None: ...        # FirstTrackOk
    def first_track_bad_prompt(self, bad: Part, reason: Reason) -> None: ...
    def first_track_transient(self, reason: Reason) -> None: ...
    def fill_ok(self, new: Part) -> None: ...               # FillOk
    def fill_bad_part(self, bad: Part, reason: Reason) -> None: ...   # finding #5
    def fill_transient(self, reason: Reason) -> None: ...
    def retry_fails(self, reason: Reason) -> None: ...
    def retry_exhausted(self, reason: Reason) -> None: ...
    def recover(self) -> None: ...
    def vibe_style_change(self, who: SessionId, new_pool: frozenset[Part]) -> None: ...
    def turn_off(self) -> None: ...

    # --- consume path (§8) — no generation, no ownership required --------
    def rotate(self) -> None: ...          # Rotate = TrackEnds = Loop
    def skip(self, who: SessionId) -> None: ...   # Rotate gated on ownership
    def play_part(self, target: Part) -> None: ...  # explicit index, no anti-repeat
    def start_from_disk(self, target: Part) -> None: ...  # CLI cold start, no owner

    # --- observation (finding #5: BOTH surfaces) -------------------------
    @property
    def mode(self) -> Mode: ...
    @property
    def status(self) -> ProgramStatus: ...   # program-level: mode + last_error
    @property
    def failed_parts(self) -> FrozenParts: ...  # per-Part surface
    @property
    def pool(self) -> tuple[Part, ...]: ...      # stable numbering (finding #7)
```

`rotate()` delegates the *choice* of the next Part to the injected
`PlaybackPolicy` (§1.4). The anti-repeat rule of `Rotate` (`playing' ≠ playing`
when `#pool ≥ 2`) is the playlist policy's contract; `play_part()` bypasses the
policy because the user named the Part (finding #7 — no anti-repeat).

**Precondition = Z guard.** `rotate`, `play_part` are enabled in
`playing_filling ∨ playing_rotating ∨ retrying` (finding #3 — playback survives a
transient backoff). `play_part`'s guard `target ∈ pool` is exactly "the addressed
index resolved to a *ready* Part"; an out-of-range index never reaches here (§4).

Why `Program` is not a god class: every one of its ~18 methods touches exactly
one attribute, `_state` (LCOM ≈ 0 — perfectly cohesive). Its size is bounded
because the invariant logic lives in `ProgramState`, not here; each method is a
guard + an `_evolve`. If `program.py` approaches the 300-line limit, the natural
split is generation-path vs consume-path into two collaborators the entity holds
— but the first cut keeps them together while the count is ~18 short methods.

### 1.3 The Store protocol + filesystem impl + in-memory fake (DI seam)

The operator's hard requirement: *all program/part persistence and disk I/O is
behind an injected Protocol, with a filesystem impl in production and an
in-memory fake for tests; zero direct filesystem access in the domain/loop.*
This generalizes today's `TrackStore`.

Two narrow protocols (PY-IC-9, PY-DP-11), in `programs/store.py`:

```python
@runtime_checkable
class ProgramStore(Protocol):
    """The set of persisted Programs. The only place the programs root is read."""
    def list_programs(self) -> tuple[ProgramManifest, ...]: ...
    def resolve(self, name: ProgramName) -> ProgramManifest | None: ...
    def open(self, name: ProgramName) -> PartStore: ...   # scoped to one Program
    def create(self, manifest: ProgramManifest) -> PartStore: ...

@runtime_checkable
class PartStore(Protocol):
    """One Program's Parts on disk. Replaces TrackStore, scoped to a directory."""
    def ready_parts(self) -> tuple[Part, ...]: ...    # ordered, ready only
    def write_target(self, index: int) -> Path: ...   # where a new Part audio lands
    def record(self, part: Part) -> None: ...         # append to manifest + fsync
    def manifest(self) -> ProgramManifest: ...
    def prepare(self) -> None: ...
```

- **Production:** `FilesystemProgramStore` (root = `~/Music/vox/programs/`) and
  `FilesystemPartStore` (one program directory + its `manifest.json`). These are
  the *only* modules importing `pathlib`/`json` for program data. `resolve`
  returns `ProgramManifest | None` because absence-by-name is the documented
  contract (PY-EH-8 / PY-TS-14 — justified inline); `open` on a missing program
  raises.
- **Fake:** `InMemoryProgramStore` / `InMemoryPartStore` in `tests/programs/
  conftest.py`, structural (no inheritance), holding dicts in memory. Generalizes
  today's `FakeTrackStore`. The domain and the fill loop touch only the protocol,
  so every domain and loop test runs filesystem-free.

`resolve`'s return of `| None` and `list_programs` reading a directory are the
`RestartFromDisk` seam: the store loads a saved pool of ready Parts, the daemon
holds the Program idle (`off`) until turned on, and the CLI plays from disk with
no generation.

### 1.4 `PlaybackPolicy` (strategy seam)

`PlaybackPolicy` is the format axis for ordering/advance (finding #1: the
loop-vs-stop choice lives in `Rotate`, not in a new mode). A single-method
strategy (PY-DP-11), `programs/playback_policy.py`:

```python
class PlaybackPolicy(Protocol):
    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> Part:
        """Return the Part to play after `playing`."""
```

- `RotatePolicy` (Phase 1, playlist): shuffle, avoid immediate repeat when
  `#pool ≥ 2`, replay the sole Part when `#pool == 1` (the only intended
  looping). This is today's `TrackPool.pick_next`, promoted to a strategy.
- Phase 2/3 add `SequentialPolicy` (podcast/audiobook: advance in index order,
  stop after the last Part). No change to `Program` or `ProgramState`.

### 1.5 `Producer` (per-format generation backend)

`Producer` is the "make one Part" backend — the seam between the format-general
fill orchestration and the format-specific ElevenLabs call. A single-method
strategy (PY-DP-11), `programs/producer.py`:

```python
class Producer(Protocol):
    async def produce(self, spec: PartSpec, target: Path) -> Part:
        """Author-to-audio for one Part: generate into `target`, return a ready Part.

        Raises ProducerBadInput on a permanent error (bad_prompt/ToS/missing key)
        and ProducerTransient on a transient one (429/quota/5xx/timeout) so the
        fill loop can route to the right Program transition (findings #4/#5).
        """
```

- `MusicProducer` (Phase 1): wraps `ElevenLabsMusicProvider.generate_track`.
  Absorbs today's `TrackGenerator.generate`. **Folds in vox-y3om** — instead of
  the hard-wired `_MUSIC_DURATION_MS = 120_000`, it samples a realistic,
  slightly-randomized length per Part (a `LengthPolicy`, playlist range e.g.
  90–210 s). This lands here because Phase 1 is already in the generation path.
- The two exception types make the model's three failure branches
  (`FillOk`/`FillBadPart`/`FillTransient`, `FirstTrackOk`/`BadPrompt`/`Transient`)
  a clean routing decision in the fill loop, not a string sniff.
- Phase 2/3 add `DialogueProducer` / `NarrationProducer`. `Program`, the store,
  the policy, and the fill loop are untouched.

### 1.6 The fill loop and the playback loop

Today's `PoolFiller` mixes two concerns: (a) the cancellable single-flight async
task machinery, and (b) the provider call. The provider call moves to `Producer`
(§1.5); the async machinery stays as `Filler` (`programs/filler.py`) — one
cancellable sequential task per active Program that drives a `Producer` and feeds
results into the Program's transitions (`fill_ok` / `fill_bad_part` /
`fill_transient`). The single-flight lock, `asyncio.shield`, and orphan-discard
logic carry over unchanged in behavior.

`ProgramLoop` (`programs/loop.py`) replaces `MusicLoop`: it owns the player
subprocess, races it against the control signal, and on track-end calls
`program.rotate()` then plays `program.playing`. It never generates.

### 1.7 Ownership / control channel

`ProgramControlChannel` (`programs/control.py`) — today's
`MusicControlChannel`, generalized in name. It carries the on/off lifecycle,
the owner `SessionId`, and the pending control signal the loop reads. See §7 for
the one substantive change here (the debt paydown): the control signal stops
being a bare string `Literal` and becomes a typed command.

---

## 2. Persistence — on-disk layout and manifest

Today: every track is a flat file in `~/Music/vox/tracks/`, and the "pool" is
inferred from a filename prefix `<vibe>_<style>_<ts>_<n>.mp3` (vox-us4g: "it's a
naming pattern, not a list"). This design replaces the pattern with an explicit,
named directory + manifest.

```
~/Music/vox/programs/
  <program-name>/
    manifest.json
    001.mp3
    002.mp3
    ...
```

`<program-name>` is the addressable identity — for a migrated playlist it is the
old pool prefix (`ambient_techno`), which stays stable so CLI, MCP, and daemon
all resolve the same entity by name.

`manifest.json` (the `ProgramManifest` value object serialized) — the minimal
schema the CLI needs to play/advance **without the daemon regenerating**:

```json
{
  "name": "ambient_techno",
  "format": "playlist",
  "subject": {"vibe": "ambient", "style": "techno"},
  "parts": [
    {"index": 1, "file": "001.mp3", "status": "ready",  "duration_ms": 132000},
    {"index": 2, "file": "002.mp3", "status": "ready",  "duration_ms": 158000},
    {"index": 3, "file": "003.mp3", "status": "failed", "reason": "bad_prompt: ..."}
  ]
}
```

- `parts[].status` promotes the model's `PartStatus` to a stored field. In Phase 1
  only `ready` and `failed` are ever written (finding #9: atomic delivery hides
  `pending`/`generating`); the schema carries them now so Phases 2–3 add long-gen
  statuses without a manifest migration.
- `subject` holds the playlist's `(vibe, style)`; podcast/audiobook store
  `topic`/`brief`/`language`/`level` here (concept §Content dimensions) — an open
  union the manifest tolerates.
- `duration_ms` is what vox-y3om produces (varied length) and what a future
  finite-format progress display needs.

Addressing is uniform across the three callers: **by name** (`ProgramName`) for
the Program, **by 1-based index** (`PartRef`) for a Part, resolved against the
manifest's ordered `parts`. The daemon holds the active `Program`; the store is
the shared source of truth all three read.

---

## 3. Migration — old → new (forward integration only)

Per PY-RF-6, callers are wired to the new path and the old path is deleted in the
same PRs. The mapping:

| Today (`voxd/music/`) | Phase 1 (`voxd/programs/`) | Disposition |
|---|---|---|
| `TrackStore` / `FilesystemTrackStore` | `PartStore`/`ProgramStore` + fs impls | **generalized + split**, old deleted |
| `TrackPool.pick_next` | `RotatePolicy.next_part` | **extracted to strategy** |
| `TrackPool.is_full`, len | folded into `ProgramState` pool invariants + `Format.pool_size` | **absorbed**, old deleted |
| `Playlist` (pool identity + fill + selection) | split: `Program`/`ProgramState` (state), `Filler` (fill), `RotatePolicy` (selection) | **decomposed**, old deleted |
| `PoolFiller` | `Filler` (async machinery) + `Producer` (provider call) | **split**, old deleted |
| `TrackGenerator.generate` | `MusicProducer.produce` (+ vox-y3om length) | **moved**, old deleted |
| `TrackGenerator` naming/slug/list | `PartStore` + manifest; naming becomes `NNN.mp3` index | **absorbed**, old deleted |
| `MusicScheduler` (29-method facade) | `Program` (transitions) + `ProgramSession`/control (ownership) + `ProgramLoop`/player (proc) | **decomposed — this is the debt paydown (§7)** |
| `MusicLoop` | `ProgramLoop` | **renamed + simplified** |
| `MusicControlChannel` | `ProgramControlChannel` (typed signal, §7) | **renamed + hardened** |
| `MusicControl` string Literal | `ControlSignal` command type | **replaced (§7)** |
| `Music*Handler` (6 wire handlers) | `Program*Handler` (list/play/loop/next/on/off/vibe) | **renamed + extended** |
| `MusicResponse`, `MusicMode`, `MusicState` | `ProgramStatus` + `Mode` (§1) | **replaced** |

**On-disk migration of existing tracks.** Real users have `~/Music/vox/tracks/
*.mp3`. This is user data, not code, so the forward path is an explicit one-time
migration (not a compat shim in the read path): on daemon start, if the legacy
`tracks/` dir exists and `programs/` does not, group flat files by prefix, `mv`
each group into `programs/<prefix>/NNN.mp3`, and write a `manifest.json` per
group. `mv`, not delete (org rule). The legacy dir is removed only after the move
verifies. This is the one place a "read the old layout" behavior exists, and it
exists to *retire* the old layout, then it is gone. **Flagged as risk R1 (§9).**

---

## 4. CLI + MCP replay surface

Consume-only on the CLI (no LLM, free playback); create+consume on MCP.

### CLI (`vox music ...`, consume-only)

| Command | Maps to | Ownership |
|---|---|---|
| `vox music list` | `ProgramStore.list_programs` | none |
| `vox music play <name>` | `StartFromDisk` (from `off`) → then auto-rotate | none (agent-unowned, finding #6) |
| `vox music play <name> playlist:2` | resolve `PartRef(2)` → `PlayPart` | none |
| `vox music loop <name>` | `StartFromDisk` + `Loop`/`Rotate` on end | none |
| `vox music next` | `Rotate` (auto-advance is the same transition) | none |

`playlist:N` part-addressing: the surface parses `<name> [format:index]`,
resolves the 1-based `index` against the manifest's ordered `parts` to a `Part`,
then calls `play_part`. **Out-of-range is a CLI resolution error reported before
any transition** (finding #7 — no "index out of range" transition exists in the
model). Exit non-zero, human message `"playlist has 3 parts; 5 is out of range"`.

Because the CLI holds no owner, an agent that later wants to `skip` or
`vibe_style_change` a CLI-started Program must `turn_on` (claim) it first
(finding #6) — the surface returns a clear error directing that, never a silent
no-op.

### MCP (`mic` tools, create + consume)

- Existing `music` (author + turn on), `music_play`, `music_list`, `music_next`
  keep working — `music` now *produces a Program* (manifest written) instead of
  loose files. Add `music_loop` and part-addressed play (`music_play` gains an
  optional `part` index).
- `status` (finding #5 — BOTH surfaces): extend the MCP `status` tool to return,
  for the active Program, `mode`, program-level `last_error` (retrying/failed),
  and the per-Part `failed_parts` list with reasons. This closes vox-ig52 for the
  Program model: a failed fill Part is observable via `status`, never only in a
  daemon log. **This is a boundary contract — a test drives `status` through the
  MCP tool and asserts both surfaces appear (per the "client-observable, not
  logs" rule).**

---

## 5. Full-scope generalization (Phases 2–3 fit without core rework)

The seams already carry the other two formats:

| Axis | Playlist (P1) | Podcast (P2) | Audiobook (P3) | Seam |
|---|---|---|---|---|
| Part backend | `MusicProducer` | `DialogueProducer` | `NarrationProducer` | `Producer` (§1.5) |
| Ordering/advance | `RotatePolicy` | `SequentialPolicy` | `SequentialPolicy` | `PlaybackPolicy` (§1.4) |
| Pool size | `Format.pool_size(playlist)=12` | `=6` | `=6` | `Format` axdef |
| Part status stored | `ready`/`failed` only | + `pending`/`generating` | + `pending`/`generating` | `PartStatus` already declared (finding #9) |
| Manifest subject | `{vibe, style}` | `{topic, language, level}` | `{brief, casting, language, level}` | `ProgramManifest.subject` open union |

`Program`, `ProgramState`, `ProgramStore`/`PartStore`, and the loops are
format-general. Phase 2/3 supply a `Producer`, a `PlaybackPolicy`, a
`pool_size`, and (finding #9) promote `pending`/`generating` to stored per-Part
state — an additive change to `PartStatus` handling and two new transitions, with
no edit to the state signature. That is the whole point of pinning the six modes
and the `PartStatus` free type now.

---

## 6. Test plan

Targets stated explicitly. Tests mirror source under `tests/programs/`.

### Coverage targets

- **Domain (pure, no I/O): ≥ 98% line + branch.** `ProgramState`, `Program`,
  `Part`/`FrozenParts`, `Format`, `Mode`, `RotatePolicy`, `Reason`,
  `ProgramManifest`. These are pure and cheap; near-total coverage is expected.
- **Store + Producer seams: ≥ 95%.** `FilesystemProgramStore`/`PartStore`
  (against `tmp_path`), `MusicProducer` (provider mocked at the boundary with
  valid MP3 bytes — `AudioSegment.silent`, per TESTING.md).
- **Loops (`Filler`, `ProgramLoop`): ≥ 90%**, driven against the in-memory fake.
- **Surfaces (CLI, MCP handlers): ≥ 90%**, including the boundary tests below.
- Overall new-package line coverage target: **≥ 95%** (`make coverage`).

### DI fakes

- `InMemoryProgramStore` / `InMemoryPartStore` — filesystem-free, in
  `tests/programs/conftest.py` (generalizes today's `FakeTrackStore`).
- `FakeProducer` — records `produce` calls; parametrized to succeed, raise
  `ProducerBadInput`, or raise `ProducerTransient`, so loop tests exercise all
  three model branches without a provider.

### Property tests — invariants asserted **by name**

Each maps to a "Key Property" in the `.tex` §9. Parametrized over reachable
states (built via the transitions), asserting after every transition:

1. `test_bounded_pool` — `len(pool) ≤ format.pool_size` always (playlist ≤ 12).
2. `test_generation_only_below_full` — `fill_ok` rejected at full; a full pool has
   `mode == playing_rotating ∧ filling is False`.
3. `test_at_most_one_fill` — `filling` ⇒ `mode ∈ {generating_first,
   playing_filling}`.
4. `test_playing_is_ready` — `playing is not None` ⇒ `playing ∈ pool`.
5. `test_failed_is_observable` — `mode == failed` ⇒ `last_error is not None`; the
   only two operations reaching `failed` set both.
6. `test_full_pool_never_hard_fails` — from any state with `#pool ≥ 1`, no
   transition sequence reaches `failed` (structural; assert `retry_exhausted`
   guard `pool == ∅`).
7. `test_two_failure_surfaces` — `fill_bad_part` records in `failed_parts` and
   leaves `last_error is None` and the Program healthy/playing (finding #5).
8. `test_replay_generates_nothing` — `play_part`, `loop`, `rotate`,
   `start_from_disk` leave `pool` and `failed_parts` unchanged and never set
   `filling` (assert `FakeProducer.produce` never called).
9. `test_no_immediate_repeat` — `rotate` with `#pool ≥ 2` gives `playing' ≠
   playing`; `play_part` deliberately may repeat.
10. `test_retry_cap_empty_pool_only` — `retry_exhausted` requires `pool == ∅`; a
    non-empty pool recovers via `recover` while playback continues.
11. `test_off_releases_ownership` — `mode == off` ⇒ `owner is None ∧ playing is
    None ∧ filling is False ∧ last_error is None ∧ failed_parts empty`.
12. `test_generation_modes_owned` — `mode ∈ {generating_first, retrying, failed}`
    ⇒ `owner is not None`; hence `vibe_style_change` is reachable from `failed`.

### Boundary / failure tests

- **CLI part-addressing:** `playlist:2` plays index 2; `playlist:99` exits
  non-zero with an out-of-range message and **no** transition (finding #7);
  `playlist:0` and malformed `playlist:x` rejected at parse.
- **Empty pool:** `music next` / `skip` on `generating_first` is a no-op
  (finding #1); `play_part` on an empty pool raises (guard `target ∈ pool`).
- **Part failed:** `fill_bad_part` surfaces via `status`' `failed_parts`; program
  keeps playing.
- **Program failed:** `first_track_bad_prompt` → `mode == failed` + `last_error`
  surfaced via `status`; recovery via `vibe_style_change` from `failed`.
- **Missing key / provider down:** `MusicProducer` with no `ELEVENLABS_API_KEY`
  fails fast at turn-on (not a silent self-disable), asserted through the handler.

### Loop-level tests

Drive the real `ProgramLoop` + `Filler` against `InMemoryPartStore` +
`FakeProducer`: turn on an empty Program → first Part delivered → plays →
auto-rotate on track-end → fill to full → stops filling at `pool_size` → rotates
forever. And the resilience path: `FakeProducer` raising `ProducerTransient` puts
the Program in `retrying` while playback continues, then `recover` re-arms the
fill. This is the transition bas7 (#291) shipped broken — advance-on-track-end —
now covered by an executable test that listens to it.

### MCP boundary test (client-observable)

Call the `status` MCP tool through the server and assert the JSON carries `mode`,
`last_error`, and `failed_parts` — verifying the failure surfaces reach a client,
not just a log.

---

## 7. OO debt paydown target

**Target:** `MusicScheduler` (`src/punt_vox/voxd/music/scheduler.py`) — a
29-method facade with three disjoint method clusters, a PL-CO-2 violation
(">3 disjoint clusters = decompose") and a classic middle-man god class. It sits
right at 295 measured lines (the ratchet's 300 ceiling), so it is also the module
most at risk of tipping over on the next change.

The three clusters (disjoint instance-state → high LCOM):

1. **Session/ownership lifecycle** — `turn_on`, `turn_off`, `update_vibe`,
   `skip_next`, `_adopt`, `_reset_session`, `disable` (touch `_channel`,
   `_state`).
2. **Pool-selection pass-throughs** — `select_first`, `select_next_track`,
   `pool_empty`, `await_first_track`, `ensure_fill`, `mark_playing`,
   `mark_generating` (thin delegations to `_playlist`).
3. **Player-process lifecycle** — `begin_playback`, `kill_proc`, `proc`,
   `take_pending_track`, `has_pending_track` (touch `_proc`, `_pending_track`).

**Transformation (Extract Class + Split Module, PY-RF-3):** the migration
dissolves this facade into three cohesive homes, each owning one cluster's state:

- cluster 1 → `Program` transitions (`turn_on`/`vibe_style_change`/`skip`/
  `turn_off`) over `ProgramState`, plus `ProgramControlChannel` for ownership.
- cluster 2 → `ProgramState` pool + `RotatePolicy` (selection) + `Filler`
  (fill/first-track handshake). The pass-through methods vanish — callers use the
  owning object directly (no middle man).
- cluster 3 → the player-process lifecycle moves to `ProgramLoop` (which already
  owns the subprocess), removing the split-ownership of `_proc` between scheduler
  and loop.

**Bundled sub-transformation (Replace Primitive with Object):** the
`MusicControl = Literal["none","off","skip","play","vibe"]` string signal drives
a conditional forest across `MusicLoop._await_first`, `_supervise`, and
`_next_track` (loop `max_complexity == 8`, the highest in the music package). It
becomes a typed `ControlSignal` on `ProgramControlChannel`, dispatched
polymorphically, collapsing those forests.

**Metrics improved (expected deltas):**

- **LCOM / responsibility-count (PL-CO-2):** `MusicScheduler`'s 3 disjoint
  clusters → three single-responsibility classes each with LCOM ≈ 0. The
  middle-man (PY-OO-7) is eliminated: ~9 pure pass-through methods deleted.
- **`max_complexity` (PL-OA-2):** loop 8 → ≤ 5 after `ControlSignal` dispatch.
- **`module_size`:** the 295-line `scheduler.py` is deleted; its responsibilities
  land in modules each well under the 300 ceiling, moving the whole area off the
  ratchet edge.
- Net: `check-oo` improves on every touched file; no metric regresses.

This is not padding bolted onto the feature — decomposing this god-facade **is**
how the migration lands cleanly. Naming it makes the ratchet paydown explicit and
measurable.

---

## 8. Proposed write-set

The design owns the write-set (PY-IC-9/PY-OO-2 — extract new modules, don't cram).
Grouped for rollback-coherent PRs.

### Create — `src/punt_vox/voxd/programs/`

- `__init__.py` — package facade, `__all__`.
- `format.py` — `Format` enum + `pool_size`.
- `mode.py` — `Mode` enum.
- `identifiers.py` — `SessionId`, `ProgramName`, `Reason` value objects.
- `part.py` — `Part`, `PartStatus`, `FrozenParts`, `PartRef`.
- `state.py` — `ProgramState` (all invariants) + `initial`/`restored`.
- `program.py` — `Program` entity (transitions) + `ProgramStatus`.
- `playback_policy.py` — `PlaybackPolicy` protocol + `RotatePolicy`.
- `producer.py` — `Producer` protocol, `PartSpec`, `MusicProducer`,
  `LengthPolicy` (vox-y3om), `ProducerBadInput`/`ProducerTransient`.
- `manifest.py` — `ProgramManifest` value object (+ (de)serialize).
- `store.py` — `ProgramStore`/`PartStore` protocols + `FilesystemProgramStore`/
  `FilesystemPartStore`.
- `filler.py` — `Filler` (async single-flight fill driving a `Producer`).
- `control.py` — `ProgramControlChannel` + `ControlSignal`.
- `loop.py` — `ProgramLoop`.
- `playback_cmd.py` — moved from `music/` (player argv; unchanged behavior).
- `migrate.py` — one-time legacy `tracks/` → `programs/` migration (§3, R1).
- handlers: `list_handler.py`, `play_handler.py`, `loop_handler.py`,
  `next_handler.py`, `on_handler.py`, `off_handler.py`, `vibe_handler.py`.

### Create — `tests/programs/`

- `conftest.py` (in-memory fakes: `InMemoryProgramStore`/`PartStore`,
  `FakeProducer`, mock WS), plus one `test_*.py` per source module and
  `test_invariants.py` (the named property tests), `test_loop_session.py`
  (loop-level), `test_cli_programs.py`, `test_mcp_status.py`.

### Modify

- `src/punt_vox/voxd/daemon.py` — wire `programs/` (store, program, producer,
  loop, handlers) in place of `music/`; run `migrate` on start.
- `src/punt_vox/server.py` — `status` tool returns Program mode + `last_error` +
  `failed_parts`; `music`/`music_play`/`music_list`/`music_next` retargeted; add
  `music_loop` + part index.
- `src/punt_vox/__main__.py` — `music` CLI group: `list`/`play <name>
  [format:index]`/`loop`/`next`, consume-only.
- `src/punt_vox/client.py` — client methods for the new/renamed wire messages.
- `src/punt_vox/voxd/__init__.py`, `voxd/daemon.py` imports — drop `music`
  re-exports, add `programs`.

### Delete (same PRs as their replacements — forward integration)

- entire `src/punt_vox/voxd/music/` package (17 modules).
- entire `tests/music/` (rewritten as `tests/programs/`).

---

## 9. Open questions / risks (top 3 for leader + operator)

**R1 — Legacy on-disk migration (highest).** Existing users have flat
`~/Music/vox/tracks/*.mp3` with no manifest. The plan (§3) is a one-time
start-up migration that groups by prefix, `mv`s into `programs/<prefix>/
NNN.mp3`, and synthesizes a manifest — `mv` not delete, legacy dir removed only
after verify. *Question:* is start-up auto-migration acceptable, or should it be
an explicit `vox music migrate` command the operator runs once? Auto is
friendlier but does disk `mv` on daemon start; explicit is safer but leaves a
window where old tracks are invisible to the new model. **Recommend:** explicit
`vox music migrate` (consume-only, no daemon coupling) + a one-line start-up log
if a legacy dir is detected. Decision needed before the store PR.

**R2 — One active Program vs. many.** The Z model is a single Program state
machine (the daemon's active music). Persistence + `list`/`play <name>` imply
many saved Programs on disk but one *active* at a time. This design takes that
reading: `ProgramStore` holds many; the daemon animates one `Program` at a time;
`play <name>` swaps which manifest backs the active Program. *Question:* confirm
Phase 1 never needs two Programs playing at once (concept §Decision 2 says no
cross-program mixing, which supports one-active). **Recommend:** one active
Program in Phase 1; revisit only if audiobook-with-bed resurfaces. Confirm.

**R3 — Part naming: index vs. custom name.** Today `/music on --name X` and
`/music play X` address a *track by custom name*. The new model addresses Parts
by **index** within a named Program (`playlist:2`). *Question:* do we keep
per-Part custom names (a `title` in the manifest, addressable as
`<program>:<title>`), or is index-only addressing sufficient for Phase 1, with
custom names surviving only as the *Program* name? **Recommend:** index-only Part
addressing for Phase 1 (`playlist:2`), custom names live at the Program level;
manifest keeps an optional `title` per Part for display without making it an
address. Confirm — this shapes the `PartRef` resolver and the handler surface.

Secondary (not blocking): `poolSize(playlist)=12` and `maxRetry=5` are the model
constants — confirm they stay config-fixed (not user-tunable) in Phase 1; the
vox-y3om length range (90–210 s) is a `LengthPolicy` default I picked — confirm
the range.
