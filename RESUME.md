# OO Refactoring Status

Last updated: 2026-07-03
Main: `9cb5d53` (post-#281). Package version: **v4.9.0** (shipped 2026-07-03, on PyPI + marketplace).

**The OO refactor is an active, ongoing goal.** This file is the authoritative hand-off for resuming it. Phase 1 and Phase 2 Step 5 are complete; Phase 2 Steps 6–11 and most of Phase 3 remain.

## What Was Done

### OO-refactor PRs merged

| PR | Title | Key changes |
|----|-------|-------------|
| #256 | OO tooling foundation | oo_score.py, OutputResolver, ruff rules |
| #257–#259 | voxd decomposition (Steps 0–10) | config/chimes/dedup/playback/synthesis/health/router/daemon — the 2,729-line `voxd.py` god-module and its 20-field `DaemonContext` eliminated |
| #260 | service decomposition + handler extraction | `service/` package (5 classes), handler modules |
| #261 | SessionConfig + DoctorCheck | server.py refactor, doctor.py extraction |
| #262 | SynthesisSpec + ConfigStore + music package | types_synthesis.py, config.py, `voxd/music/` |
| #263 | server dedup + playback/synthesis cleanup | `_process_segments`, SessionConfig encapsulation, `play_audio` decomposition |
| #264 | Phase E/F/G + coupling fixes + dead code | OutputFormatter, ApiKeyResolver, AudioMigration, DaemonRestarter, provider instance voice caches, 315 lines dead code removed, 3 coupling cycles broken |
| #265 | tooling | max-complexity gate tightened to 10 |
| #267 | v3 Phase 1 Step 1: CacheKey | `cache.py` (text,voice,provider) → `CacheKey` |
| #268 | resolve.py coverage | `test_resolve.py`, 50%→100% |
| #269 | v3 Phase 1 Steps 2–4 | PlaybackResult, MusicTrack, HookPayload value objects |
| #270 | v3 Phase 2 Step 5 | **Signal + SignalLog extracted from hooks.py** |

### Non-refactor PRs merged since (context, not OO work)

PRs #273 (PEP 735 dep-groups), #274 (LaunchDaemon→LaunchAgent, DES-038), #275 (repo name from cwd), #276 (remove migration + install health check), #280 (hook config-IO + stdin-error hardening), and #281 (ethos submodule bump + OO rebaseline). The v4.9.0 release shipped 2026-07-03.

## Current Metrics (main @ 9cb5d53)

The ratchet (`make check-oo`) is green on touched files, but the codebase has **not** converged — 14 files remain over the `module_size` 300 threshold and two procedural hotspots still fail outright:

- **`__main__.py`** — ~1,035 (metric) / 1,220 (wc) lines, `max_complexity` and `module_size` FAIL, `method_ratio` low.
- **`server.py`** — ~850 (metric) / 1,030 (wc) lines, same failures.
- Other over-threshold files: `normalize.py`, `client.py`, `doctor.py` (`init_violations 1`, `classes_per_module 5`), `synthesis.py`, `playback.py`, `music/loop.py`.
- Note: the git-diff-based ratchet proves "this diff didn't regress touched files," not "the codebase converged." `installer.py` and `core.py` sit pinned exactly at `module_size` 300; adjacent-file drift accumulates until a `--rebaseline` absorbs it (last done in #281).

## What Remains — v3 Execution Plan

Full plan: `docs/oo-refactor/oo-execution-plan-v3.md`. Hidden classes: `docs/oo-refactor/hidden-classes-analysis.md`.

### Phase 1: Value Objects (Steps 1–4) — ✅ COMPLETE

| Step | Class | Status |
|------|-------|--------|
| 1 | CacheKey | **DONE** (#267) |
| 2 | PlaybackResult | **DONE** (#269) |
| 3 | MusicTrack | **DONE** (#269) |
| 4 | HookPayload | **DONE** (#269) |

### Phase 2: Domain Objects (Steps 5–11) — 1 of 7 done

Dependencies: Step 6 depends on 5. Step 9 depends on 8. Step 11 depends on 8 and 9.

| Step | Class | Files | Status |
|------|-------|-------|--------|
| 5 | Signal + SignalLog | signal.py, hooks.py, config.py | **DONE** (#270) |
| 6 | Vibe | vibe.py, 5+ files (data carrier only) | **NOT STARTED** — next up |
| 7 | Voice + VoiceResolver | voice.py, voices.py, resolve.py, server.py | NOT STARTED |
| 8 | SynthesisSpec behavior | types_synthesis.py, client.py, server.py, synthesis.py | NOT STARTED |
| 9 | Segment | segment.py, server.py, `__main__.py` | NOT STARTED |
| 10 | Notification | notification.py, hooks.py | NOT STARTED |
| 11 | Utterance | utterance.py + 5 files | NOT STARTED |

`vibe.py`, `voice.py`, `segment.py`, `notification.py`, `utterance.py` do not exist yet — these are the remaining extractions.

### Phase 3: Complexity Reduction (Steps 12–17) — blocked on Phase 2

| Step | What | Status |
|------|------|--------|
| 12 | audio_migration CC reduction | NOT STARTED |
| 13 | server.py CC reduction | NOT STARTED (blocked on Steps 8–11) |
| 14 | `__main__.py` CC reduction | NOT STARTED (blocked on Steps 8–11) |
| 15 | chunked synthesis helper | DONE |
| 16 | delete music_handlers.py | DONE |
| 17 | `__all__` on every module | NOT STARTED — 50 of 77 source files have it; **27 remain** (incl. `server.py`, `__main__.py`, `client.py`, `hooks.py`, every `service/` module) |

## How to Resume

1. **Next step: Phase 2 Step 6 (Vibe).** Step 5 (Signal) is merged, which unblocks it. Work Steps 6–11 to completion — these are the domain-object extractions.
2. Steps 13–14 (the `server.py` / `__main__.py` complexity reduction — the two worst files) are **dependency-blocked on Steps 8–11**. Do Phase 2 first; the hotspot cleanup falls out of it.
3. Step 17 (`__all__`) is independent and can be done any time — 27 files remain.
4. Follow the development loop below. One mission per agent, never batch steps. Delegate implementation to `rmh` (Python core); peer-review designs with `rej`.

## Development Loop

### Inner loop — one mission

1. Delegate to specialist (one focused task)
2. `make check` — must pass
3. `feature-dev:code-reviewer` on the diff
4. `pr-review-toolkit:silent-failure-hunter` on the diff
5. Fix every finding — both agents must return zero findings; re-run
6. Commit

### Outer loop — one PR

1. `make check` on full accumulated diff
2. Both review agents on complete diff; fix all findings
3. Push PR

### Key rules

- **The COO does not write code.** Every code change is delegated.
- **One mission = one task.** Never batch multiple steps per agent.
- **PR boundaries by rollback granularity**, not size. Phase 2 steps can share one PR if the diff is coherent.

## Reference Documents

| Document | What it contains |
|----------|-----------------|
| `docs/oo-refactor/oo-execution-plan-v3.md` | Complete 17-step plan |
| `docs/oo-refactor/hidden-classes-analysis.md` | 7 hidden domain classes (Signal ✅, Vibe, Voice, SynthesisSpec, Segment, Notification, Utterance) |
| `docs/oo-refactor/STATUS.md` | Phase-by-phase status snapshot (kept in sync with this file) |
| `docs/oo-refactor/package-restructure-design.md` | Coupling analysis — why no new packages |
| `docs/oo-refactor/music-package-design.md` | Music subsystem redesign (done) |
