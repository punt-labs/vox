# Resume — vox

**Updated:** 2026-07-07. **Branch:** `main` (Phase 1 merged).

## Status: Audio Programs Phase 1 is SHIPPED ✅

PR **#299** squash-merged to `main` (`80d8a18`), 2026-07-07. Nothing in flight. `bd close vox-oayr` done. Recap email sent.

### What shipped
Ownership-free, persisted **Program** model for background music + playlist replay (CLI + MCP). Single-writer `ControlChannel` + typed `ControlSignal`s (O2); `ProgramState` with 16 invariants by construction (executable Z model `docs/audio-programs.tex`). `/music on` fills a 12-track pool, auto-advances, rotates at zero credits; `/music play|list|next|loop|playlist:N` replay saved pools. Storage `~/Music/vox/<name>/` (no `programs/` segment) with **ID3 tags** on every track. Whole-tree migration/compat purge (org no-migration rule; suppression ratchet −10). Docs: README Background Music, DES-041, CHANGELOG, design doc reconciled + `docs/design-review-phase1.md` (rej+gvr).

### Verified
`make check` green (2009 tests). **Operator-eared audio flight passed** (trance pool: audible, genuine, gapless). Live: generated track at `~/Music/vox/lofi/001.mp3` with all six ID3 frames; full transition chain via the status API.

### Review outcome
7 genuine bugs caught pre-merge by Cursor/Bugbot (all real, none false): path-traversal in program names; `playlist:N`/status resolving by list-position vs intrinsic index; retry-machine stall + at-cap-non-empty hole; stale-fill orphan race across a program switch; `music_mode` shadow desync. Plus a real Z-model hole (capped-retry self-loop with no schema).

## Pending (next sessions)

**Ratchet discussion (operator-requested, hold-until-ship — now due).** Blind spots hit this session: per-commit-vs-tip `check-oo` scoring (broken intermediate commits that pass at the tip); `update-oo` (whole-tree, refuses ANY regression) vs `check-oo` (git-diff-scoped) mismatch; god-module rebaseline churn (server.py perturbs 4 relative metrics on any change); the `method_ratio`/`class_to_func_ratio` penalty on legitimately-functional modules. Multiple scoped rebaselines were needed just to ship correctness fixes.

**Follow-up beads (filed):**
- `vox-q7vh` — per-vibe music pools (**direction A**, operator-chosen). Today vibe only flavors the agent's prompts; it does NOT partition pools (`_name_for` keys on style/name; `_subject_for` copies style into `subject.vibe`; `VibeStyleChange` signal exists but is unwired). Make vibe part of the pool identity + wire it + model in the `.tex`.
- `vox-pjd8` — `install-desktop` writes the ElevenLabs key in plaintext to `claude_desktop_config.json` (security). Its stale `tts`→`vox` naming was already fixed.
- `vox-k1ee` — reconcile `architecture.tex` (Background Music subsection still describes ownership/`tracks/` layout/`voxd/music`) + rebuild the tracked `architecture.pdf`.
- `vox-kne8` — `vox daemon restart` fails first call (launchd double-bootstrap), works on retry.
- Retry-cluster extraction from `program.py` (the proper OO paydown behind the authorized rebaseline: extract first_track_transient/fill_transient/retry_fails/retry_exhausted/retry_capped/recover + relocate `GuardViolationError`).
- **jms**: add the `RetryCapped` operation schema to `docs/audio-programs.tex` (precond mode=retrying ∧ attempts=maxRetry ∧ pool≠∅; frames all; self-loop). Note: the stale-fill orphan guard needs NO model change (daemon concurrency contract, out of model scope).
- Reconcile stale OO baseline mismatches on `filesystem_store`/`identifiers`/`loop` (flagged by whole-tree update-oo, outside any single commit's diff).

## Session lessons (in memory)
- **Never TaskStop on a transcript-mtime heuristic** — output-file mtime reflects last token emission, not activity; a live agent can be silent 10-20 min. Reliable death signals: the `failed`/`killed` task-notification, or no tracked-file edits over a LONG window (10+ min). I killed a producing agent this way (2nd offense).
- **One code agent per shared tree.** Parallel agents corrupt each other's tree (caused a git divergence + a network death this session). Reconcile divergences with `reset --soft onto remote` → single fast-forward commit, not force-push.

## Loose ends (harmless)
- An orphaned `git stash` (a10980a6 set.intersection polish, superseded by shipped code) — droppable.
- Uncommitted `.punt-labs/ethos/*` mission records (session bookkeeping; incl. `m-2026-07-05-004` never formally closed via `ethos mission result` due to the write_set-vs-authorized-paths mechanics).
