# OO Refactoring Status

Last updated: 2026-05-15
Main: `f9cedd4` (PR #268 merged — resolve.py coverage)
Open PR: #269 — Phase 1 Steps 2-4 (PlaybackResult, MusicTrack, HookPayload)

## What Was Done

### PRs Merged (11 total)

| PR | Title | Key changes |
|----|-------|-------------|
| #256 | OO tooling foundation | oo_score.py, OutputResolver, ruff rules |
| #257 | voxd wave 1 (Steps 0–3) | config.py, chimes.py, dedup.py |
| #258 | voxd wave 2 (Steps 4–7) | playback.py, track_generator.py, synthesis.py, music_scheduler.py |
| #259 | voxd wave 3 (Steps 8–10) | health.py, router.py, daemon.py — DaemonContext eliminated |
| #260 | service decomposition + handler extraction | service/ package (5 classes), handler modules |
| #261 | SessionConfig + DoctorCheck | server.py refactor, doctor.py extraction |
| #262 | SynthesisSpec + ConfigStore + music package | types_synthesis.py, config.py, voxd/music/ |
| #263 | server dedup + playback/synthesis cleanup | `_process_segments`, SessionConfig encapsulation, `play_audio` decomposition |
| #264 | Phase E/F/G + coupling fixes + dead code | OutputFormatter, ApiKeyResolver, AudioMigration, DaemonRestarter, provider voice caches, 315 lines dead code removed |
| #265–266 | Tooling + docs | max-complexity→10, RESUME.md |
| #267 | Phase 1 Step 1: CacheKey | cache.py (text,voice,provider) → CacheKey dataclass |
| #268 | resolve.py coverage | test_resolve.py, 50%→100% |

### Open PR #269 — Phase 1 Steps 2-4 (pending CI)

- PlaybackResult: `{file,rc,elapsed_s,stderr,ts}` dict → frozen dataclass
- MusicTrack: generator.py track dict → dataclass with `from_stat()`, `from_dict()`, `display_line()`, `to_dict()`
- HookPayload: `StopPayload`, `BashPayload`, `NotificationPayload` with `parse()` classmethods; hooks.py handlers take typed payloads

## Current Metrics (on main, before #269)

### OO Score (6 pass, 5 fail)

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| method_ratio | 0.67 | >= 0.80 | FAIL |
| encapsulation_ratio | 1.00 | >= 1.0 | PASS |
| avg_params | 1.09 | <= 4.0 | PASS |
| max_complexity | 21 | <= 10 | FAIL |
| avg_complexity | 2.67 | <= 5.0 | PASS |
| module_size | 1035 | <= 300 | FAIL |
| classes_per_module | 5 | <= 3 | FAIL |
| class_to_func_ratio | 0.59 | >= 0.5 | PASS |
| init_violations | 1 | == 0 | FAIL |
| public_attr_violations | 0 | == 0 | PASS |
| future_annotations | 1 | == 1 | PASS |

**Worst files**: `__main__.py` (1035 lines, CC=21, method_ratio 0.0), `client.py` (init_violations 1)

## What Remains — v3 Execution Plan

Full plan: `docs/oo-refactor/oo-execution-plan-v3.md`
Hidden classes analysis: `docs/oo-refactor/hidden-classes-analysis.md`

### Phase 1: Value Objects (Steps 1–4)

| Step | Class | Status |
|------|-------|--------|
| 1 | CacheKey | **DONE** (PR #267) |
| 2 | PlaybackResult | **DONE** (PR #269, pending merge) |
| 3 | MusicTrack | **DONE** (PR #269, pending merge) |
| 4 | HookPayload | **DONE** (PR #269, pending merge) |

### Phase 2: Domain Objects (Steps 5–11)

Dependencies: Step 6 depends on Step 5. Step 9 depends on Step 8. Step 11 depends on 8 and 9.

| Step | Class | Files | Status |
|------|-------|-------|--------|
| 5 | Signal + SignalLog | signal.py, hooks.py, config.py | NOT STARTED |
| 6 | Vibe | vibe.py, 5+ files (data carrier only) | NOT STARTED |
| 7 | Voice + VoiceResolver | voice.py, voices.py, resolve.py, server.py | NOT STARTED |
| 8 | SynthesisSpec behavior | types_synthesis.py, client.py, server.py, synthesis.py | NOT STARTED |
| 9 | Segment | segment.py, server.py, `__main__.py` | NOT STARTED |
| 10 | Notification | notification.py, hooks.py | NOT STARTED |
| 11 | Utterance | utterance.py + 5 files | NOT STARTED |

### Phase 3: Complexity Reduction (Steps 12–17, blocked on Phase 2)

| Step | What | Status |
|------|------|--------|
| 12 | audio_migration CC reduction | NOT STARTED |
| 13 | server.py CC reduction | NOT STARTED |
| 14 | `__main__.py` CC reduction | NOT STARTED |
| 15 | chunked synthesis helper | DONE |
| 16 | delete music_handlers.py | DONE |
| 17 | `__all__` on every module | NOT STARTED |

## How to Resume

1. **Merge PR #269** first. All CI checks must pass. Local review is clean.

2. **Start Phase 2 Step 5** (Signal + SignalLog). Follow the development loop:
   - Bead → branch → design doc → peer review (rej) → implement (rmh, one task) → make check → both review agents → fix → re-run → commit → PR
   - **One mission per agent. Never batch steps.**

3. **PR boundaries**: batch multiple steps per PR. Phase 2 steps can be one PR if they share a branch and the diff is coherent.

## Development Loop (from quarry's CLAUDE.md)

### Inner loop — one mission

1. Delegate to specialist (one focused task)
2. `make check` — must pass
3. `feature-dev:code-reviewer` on the diff
4. `pr-review-toolkit:silent-failure-hunter` on the diff
5. Fix every finding — both agents must return zero findings
6. Re-run both agents
7. Commit

### Outer loop — one PR

1. `make check` on full accumulated diff
2. Both review agents on complete diff
3. Fix all findings
4. Push PR

### Key rules

- **The COO does not write code.** Every code change is delegated.
- **One mission = one task.** Never batch multiple steps per agent.
- **PR boundaries by rollback granularity**, not size.

## Reference Documents

| Document | What it contains |
|----------|-----------------|
| `docs/oo-refactor/oo-execution-plan-v3.md` | Complete 17-step plan |
| `docs/oo-refactor/hidden-classes-analysis.md` | 7 hidden domain classes (Signal, Vibe, Voice, SynthesisSpec, Segment, Notification, Utterance) |
| `docs/oo-refactor/package-restructure-design.md` | Coupling analysis — why no new packages |
| `docs/oo-refactor/music-package-design.md` | Music subsystem redesign (done) |
