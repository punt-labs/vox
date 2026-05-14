# OO Refactoring Status

Last updated: 2026-05-14
Branch: `refactor/phase-efg-coupling` (PR #264, open)
Main: `2b47d7a` (PR #263 merged)

## What Was Done

### PRs Merged (main)

| PR | Title | Key changes |
|----|-------|-------------|
| #256 | OO tooling foundation | oo_score.py, OutputResolver, ruff rules |
| #257 | voxd wave 1 (Steps 0–3) | config.py, chimes.py, dedup.py |
| #258 | voxd wave 2 (Steps 4–7) | playback.py, track_generator.py, synthesis.py, music_scheduler.py |
| #259 | voxd wave 3 (Steps 8–10) | health.py, router.py, daemon.py — DaemonContext eliminated |
| #260 | service decomposition + handler extraction | service/ package (5 classes), handler modules |
| #261 | SessionConfig + DoctorCheck | server.py refactor, doctor.py extraction |
| #262 | SynthesisSpec + ConfigStore + music package | types_synthesis.py, config.py, voxd/music/ |
| #263 | server dedup + playback/synthesis cleanup | `_process_segments`, SessionConfig encapsulation, `play_audio` decomposition, `_api_key_context` |

### PR #264 (Open, Not Yet Merged)

Contains everything from Phase E/F/G plus dead code removal:

- **Coupling fixes**: VOX_DATA_DIR moved to paths.py, watcher routes
  through VoxClientSync, doctor circular dep broken
- **Phase E**: OutputFormatter, ApiKeyResolver, AudioMigration,
  DaemonRestarter extracted from `__main__.py` and wired
- **Phase F**: Provider voice caches moved to instance attributes
  (elevenlabs, polly, say, espeak)
- **Phase G**: client.py `__new__` conversion, chime.py wired into watcher
- **Dead code**: 22 service shims, generate_audios protocol method,
  dead keys/dirs/paths functions, voxd re-exports pruned
- **Other agent's work included**: VoiceResolver, chunked synthesis,
  convert.py, local_play.py, ProviderRegistry, applet removal,
  suppression ratchet, oo_coupling.py tool

## Current Metrics

### OO Score (6 pass, 5 fail)

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| method_ratio | 0.66 | >= 0.80 | FAIL |
| encapsulation_ratio | 1.00 | >= 1.0 | PASS |
| avg_params | 1.11 | <= 4.0 | PASS |
| max_complexity | 30 | <= 10 | FAIL |
| avg_complexity | 2.69 | <= 5.0 | PASS |
| module_size | 1232 | <= 300 | FAIL |
| classes_per_module | 5 | <= 3 | FAIL |
| class_to_func_ratio | 0.59 | >= 0.5 | PASS |
| init_violations | 1 | == 0 | FAIL |
| public_attr_violations | 0 | == 0 | PASS |
| future_annotations | 1 | == 1 | PASS |

### Coupling Score (2 pass, 3 fail)

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| efferent_coupling | 19 | <= 7 | FAIL |
| public_names | 51 | <= 15 | FAIL |
| circular_imports | 0 | == 0 | PASS |
| max_lcom | 1.0 | <= 0.8 | FAIL |
| avg_lcom | 0.28 | <= 0.5 | PASS |

### Codebase Size

- 74 source files in src/punt_vox/
- 1533 tests passing
- 24,639 total source lines

## What Remains — v3 Execution Plan

The complete plan is in `docs/oo-refactor/oo-execution-plan-v3.md`.
The hidden classes analysis is in `docs/oo-refactor/hidden-classes-analysis.md`.

### Phase 1: Value Objects (Steps 1–4)

No dependencies between steps. Can run in parallel.

| Step | Class | Files | Status |
|------|-------|-------|--------|
| 1 | CacheKey | cache.py, synthesis.py | NOT STARTED |
| 2 | PlaybackResult | playback.py, health.py, daemon.py, speech_handlers.py | NOT STARTED |
| 3 | MusicTrack | music/track.py, server.py, `__main__.py` | NOT STARTED |
| 4 | HookPayload | hook_payload.py, hooks.py | NOT STARTED |

### Phase 2: Domain Objects (Steps 5–11)

Dependencies: Step 6 depends on Step 5. Step 9 depends on Step 8.
Step 11 depends on Steps 8 and 9.

| Step | Class | Files | Status |
|------|-------|-------|--------|
| 5 | Signal + SignalLog | signal.py, hooks.py, config.py | NOT STARTED |
| 6 | Vibe | vibe.py, 5+ files. Data carrier only. | NOT STARTED |
| 7 | Voice + VoiceResolver | voice.py, voices.py, resolve.py, server.py | NOT STARTED |
| 8 | SynthesisSpec behavior | types_synthesis.py, client.py, server.py, synthesis.py | NOT STARTED |
| 9 | Segment | segment.py, server.py, `__main__.py` | NOT STARTED |
| 10 | Notification | notification.py, hooks.py | NOT STARTED |
| 11 | Utterance | utterance.py, client.py, server.py, `__main__.py`, synthesis.py, speech_handlers.py | NOT STARTED |

### Phase 3: Complexity Reduction (Steps 12–17)

Blocked on Phase 2.

| Step | What | Files | Status |
|------|------|-------|--------|
| 12 | audio_migration CC reduction | audio_migration.py | NOT STARTED |
| 13 | server.py CC reduction | server.py | NOT STARTED |
| 14 | `__main__.py` CC reduction | `__main__.py` | NOT STARTED |
| 15 | chunked synthesis helper | providers/ | DONE (other agent) |
| 16 | delete music_handlers.py | voxd/ | DONE (already deleted) |
| 17 | `__all__` on every module | all modules | NOT STARTED |

### Worst Files (where to focus)

| File | Lines | Key failures |
|------|-------|-------------|
| `__main__.py` | 1232 | method_ratio 0.0, max_complexity 30, module_size |
| `server.py` | ~800 | max_complexity 19 |
| `voxd/synthesis.py` | 472 | module_size, method_ratio |
| `watcher.py` | 397 | method_ratio 0.375, max_complexity |
| `voxd/music/loop.py` | 385 | module_size |
| `client.py` | ~625 | classes_per_module 5 |

## How to Resume

1. **Merge PR #264 first.** It has 4 commits on `refactor/phase-efg-coupling`.
   CI should be passing. Resolve review threads and merge.

2. **Start Phase 1 (Steps 1–4).** These are small, independent value
   object extractions. Each is 1–2 files. CacheKey is the smallest.
   All can run in parallel on separate branches.

3. **Then Phase 2 (Steps 5–11).** Start with Signal/SignalLog (Step 5)
   because Vibe (Step 6) depends on it. Voice (Step 7) and SynthesisSpec
   (Step 8) are independent. Utterance (Step 11) comes last — it depends
   on SynthesisSpec and Segment.

4. **Then Phase 3 (Steps 12–17).** Extract Method on the remaining
   complex functions. This is mechanical once the domain objects exist.

## Key Design Decisions

- **Vibe is a data carrier only.** It does NOT absorb
  apply_vibe_for_synthesis (that needs provider knowledge) or
  resolve_tags (that belongs on SignalLog). See peer review in
  oo-execution-plan-v3.md.

- **Utterance is the request, not the lifecycle.** Text, SynthesisSpec,
  and request_id. Does NOT own output path, playback, or result.
  See investigation results in oo-execution-plan-v3.md Step 11.

- **No new packages needed.** Coupling analysis (package-restructure-
  design.md) concluded the flat layout is correct after the 3 coupling
  fixes. The real problems are hidden domain classes, not missing
  directory boundaries.

- **ProviderSelection dropped.** Subset of SynthesisSpec fields.
  Expressed as SynthesisSpec.resolved() factory method instead.

- **HealthStatus dropped.** DaemonHealth already returns the data,
  consumers immediately serialize. No second consumer.

- **ConfigField dropped.** Two frozensets + one function is simpler
  than 9 dataclass instances with validator callables.

## Reference Documents

| Document | What it contains |
|----------|-----------------|
| `oo-execution-plan-v3.md` | Complete 17-step plan with peer review revisions |
| `hidden-classes-analysis.md` | 7 hidden domain classes identified by rej |
| `package-restructure-design.md` | Coupling analysis — why no new packages |
| `oo-design-assessment-v2.md` | Initial per-file assessment |
| `music-package-design.md` | Music subsystem redesign (done) |
| `oo-refactoring-plan.md` | Original 52-step plan (Steps 0–17 done) |

## Coordination

- **Other agent (tty252)**: Works on voxd/music/ files and provider
  refactoring. Do not touch their files without biff coordination.
- **Biff**: Always reply to messages. Use `/biff:read` to check inbox.
- **Shared files**: voxd/`__init__`.py, voxd/daemon.py, pyproject.toml —
  coordinate via biff before editing.
