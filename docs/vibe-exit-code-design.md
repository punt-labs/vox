# Auto-Vibe on Exit Codes — Design

**Bead:** vox-ek1m · **Status:** DESIGN (under review) · **Formal model:** `docs/vibe-exit-code.tex`

## Problem

Auto-vibe derives the session's TTS mood from a classifier that pattern-matches
command **output** for pytest/ruff/git-specific tokens (`N passed`,
`All checks passed`, `[branch sha]`, `pull/N`). Three faults:

1. **Narrow.** It only recognizes this repo's Python/git toolchain. `cargo`,
   `go`, `npm`, `make`, `terraform`, `tsc` — none of their success output is
   recognized. vox is a language-agnostic TTS tool; the vibe must be too.
2. **Asymmetric.** A failure has a generic fallback (any non-zero exit →
   `cmd-fail`). A success does **not** — a clean exit with no recognized token
   yields *no signal*. So in any non-pytest/ruff workflow, wins go uncounted
   while losses still count, and the mood skews frustrated in every repo but
   this one.
3. **Fragile.** Output format drift breaks it — e.g. `-qq` suppresses pytest's
   summary line, so a passing run registers nothing.

`vox-p0u6` (the "always frustrated" bug) was the acute symptom. This is the root:
mood was keyed on *output text*, which is neither generic nor symmetric.

## Decision

**The signal is the exit code, nothing else.** Per Bash command: `exit 0 → ok`,
`exit non-zero → fail`. Delete the output-pattern matching and the entire typed
signal vocabulary (`tests-pass`/`lint-pass`/`git-commit`/`pr-created`/…).

We do **not** regex the command to judge "significance." A neutral `ls` exiting
0 correctly reads as "nothing's wrong"; a rare benign non-zero (`grep` no-match)
is one outcome absorbed by a rolling window. A cluster of failures is a bad
stretch; a run of clean commands is a calm one. That is the whole signal.

Rationale (operator, 2026-07-12): *"A bunch of error exit codes is an issue and
a bunch of clean ones are fine. The exit code is simple and generic."*

## Signal + accumulation

- The `PostToolUse` Bash hook records **one outcome per command**: `ok | fail`,
  from the exit code. A command with no exit code (the transcript-only watcher
  path) contributes nothing.
- Outcomes accumulate in the existing rolling window, capped at `MAX` (~20,
  FIFO eviction). No new persistent state.

## Mood — a stateless arc, default happy

The mood is a **total function of the window's trailing run** — no stored mood,
no separate state machine. Let `run` = the length of the trailing run of
consecutive `fail` at the end of the window (0 if the last outcome is `ok`), and
`recentFail` = a `fail` exists within the last `K` (~3) outcomes.

| Trailing window | Mood | Tags |
|---|---|---|
| clean — no fail in the last `K` | **happy** *(default)* | `[happy]` |
| `run` 1–2 | **focused** | `[focused]` |
| `run` 3–4 | **frustrated** | `[frustrated] [sighs]` |
| `run` ≥ 5 | **weary** | `[weary]` |
| last is `ok`, but a fail within the last `K` | **relieved** | `[relieved]` |

```text
happy ──fail──▶ focused ──more──▶ frustrated ──sustained──▶ weary
   ▲                                                          │
   └────── happy ◀──clean──── relieved ◀────── ok ────────────┘
```

The arc falls out of the fail-depth without tracking prior mood:

- **Default is happy**, not neutral or frustrated — the exact inversion of the
  bug the whole session chased.
- **Degrades by depth**: one stumble = focused (heads-down), a few = frustrated,
  a grind = weary.
- **Recovers**: the first `ok` after a bad stretch = relieved; as more clean
  commands push the fails out of the last `K`, it decays naturally back to
  happy.

Thresholds (`2 / 4 / 5`, `K ≈ 3`, `MAX ≈ 20`) are starting values, tunable. The
formal model `docs/vibe-exit-code.tex` pins the derivation and its invariants;
its Findings section is the record of any boundary/ambiguity the formalization
surfaced (to be resolved in this design review before implementation).

## Consequences / trade-offs

- **Lost: milestone excitement.** Exit-code-only cannot tell a PR merge from an
  `ls` — both exit 0 — so there is no `[excited]`-on-a-win peak. Accepted for
  simplicity. If wanted back later, a thin *optional* "was this a push/pr" hint
  could add it — explicitly **not** in v1.
- **Chimes — the loss is theoretical, not real.** The design review found there
  is **no live per-signal chime differentiation and no live mood pitch-shift**:
  the live resolver (`ChimeResolver.resolve(signal)`) is mood-blind and only the
  notification chimes fire in practice. The per-signal/mood machinery is
  reachable only from the unwired watcher. So collapsing to notification-chimes
  retires dead code, it does not remove a capability users have. The meaningful
  vibe signal is the ElevenLabs `vibe_tags` applied to synthesized speech (stop
  hook → `resolve_tags` → `vibe_tags`), which this redesign **preserves**.
  Mood-tinted chimes, if ever wanted, are net-new work (wire the five-mood
  vocabulary into the live `ChimeResolver`) — explicitly out of scope for v1.

## What gets deleted

Forward integration only — removed in the same change, not shimmed (PL-PP-1).
The design-review evaluator (rop) traced the consumers; the full delete-set is:

- `command_signal.py` — the whole classifier (marker tables, pytest/ruff/git
  regexes, the typed signal vocabulary, `signal_names()`). `hooks.py` imports it
  at module top, so it cannot be deleted without rewriting the accumulator.
- **`watcher.py` + `test_watcher.py`** — the `notify=c` "milestone announcement"
  feature (`SessionWatcher`, speaks "Tests passed"/"Code pushed" or plays a
  per-milestone chime). It classifies **transcript text that has no exit code**
  (`classify_output` calls the classifier with `exit_code=None`), so it is
  fundamentally incompatible with an exit-code signal. It is **unwired** —
  referenced only from its own test, never from `voxd`/`server`/`hooks`/`__main__`
  — so retiring it is clean. (Confirm with the operator this dormant feature is
  being retired, not resurrected.)
- **`chime.py` + `mood.py`** (`resolve_chime_path`, `classify_mood`,
  `MOOD_FAMILIES`) — the mood-pitch-shift mechanism. It is reachable **only**
  from the unwired watcher, and it is already broken for the new vocabulary
  (`MOOD_FAMILIES` maps only `happy`/`frustrated`; `focused`/`weary`/`relieved`
  fall through to neutral). Dead code — delete it with the watcher.
- From `voxd/chimes.py::_CHIME_MAP` (**live** — do NOT delete wholesale): remove
  **only** the four typed milestone entries (`tests-pass`, `lint-pass`,
  `git-push-ok`, `merge-conflict`) and their orphaned assets
  (`chime_tests_pass.mp3`, `chime_lint_*.mp3`). The notification chimes
  (`done`/`prompt`/`acknowledge`/`compact`/`subagent`/`farewell`) are orthogonal
  to the vibe and **stay untouched**.
- `signal.py`'s `SignalLog` is replaced wholesale by the exit-code window; the
  `vibe_signals` config field is reused, now carrying `ok`/`fail` tokens.

## Escape hatch

If the exit-code version still doesn't land in practice, **delete the
deterministic hook path entirely** and replace it with an agent-driven vibe:
periodically hint the agent to set the vibe from the transcript using its own
judgment (async, LLM, not a per-command hook). This is the documented fallback,
chosen by the operator — recorded here so it isn't re-derived later.

## Rejected alternatives

| Alternative | Rejected because |
|---|---|
| Output-pattern classification (current) | Narrow, asymmetric, fragile (see Problem). |
| Command-verb regex for "significance" | Adds complexity for little gain — the operator's call: a bad exit is an issue and a clean one is fine, regardless of what the command was. |
| LLM per command | The hook fires on every Bash command, must be near-instant, and runs outside the model's context — too slow/expensive. (An async agent-from-transcript is the *escape hatch*, not the per-command path.) |

## Formal model findings (`docs/vibe-exit-code.tex`, fuzz `-t` clean)

The Z model confirmed the design and tightened two things into hard constraints:

- **Empty window → happy is a *theorem*,** not an axiom (`runFail = 0`,
  `recentFail` false → the clean clause fires). The implementation must **not**
  code an empty-window special case — "never default to frustrated" is achieved
  by having no default at all.
- **`focusFrom = 1` is forced**, not a tunable. At `0` the mood is
  over-determined (a `run = 0` window matches happy/relieved *and* focused →
  inconsistent); at `≥ 2` it is not total (runs in `1..focusFrom-1` match no
  clause). Only `1` partitions cleanly. Entry to `focused` at `run ≥ 1` is not
  a choice.
- **`wearyFrom < maxWindow` is a load-bearing invariant.** FIFO eviction reads
  the suffix, so it changes the mood only when the whole window is failures, and
  it caps `run` at `maxWindow` — which still reads `weary` *because* `5 < 20`.
  Eviction can therefore only move the mood in the recovery direction, never
  deeper and never out of the table.
- Thresholds are unambiguous half-open bands `[1,3) [3,5) [5,∞)`; `recentK`
  tunes only relief-decay speed (relief ends `recentK` clean commands after the
  last failure). Recommended constants: `focusFrom=1` (forced), `frustFrom=3`,
  `wearyFrom=5`, `recentK=3`, `maxWindow=20`.

**Implementation must** parameterise the derivation on exactly those five
constants and assert `focusFrom = 1` and `wearyFrom < maxWindow` — the two
properties whose violation silently breaks totality or masks `weary`.

## Open decisions for the operator

1. **`focused` on a single stray non-zero.** Because `focusFrom=1` is forced, the
   *first* benign non-zero (a lone `grep` miss with no `ok` right after) reads as
   `focused` — there is no constant that makes `run=1` read `happy`. Fine if
   `focused` is a mild, heads-down voice; a problem only if `focused` is heard as
   negative. **Recommend:** acceptable — `focused` is engaged, not unhappy.
2. **Retire the unwired `notify=c` milestone-announcement watcher.** It's
   incompatible with exit-code-only and currently unused. **Recommend:** delete
   (forward integration). Confirm it isn't slated for resurrection.
3. **Thresholds** `(frustFrom=3, wearyFrom=5, recentK=3, maxWindow=20)`,
   `focusFrom=1` forced. **Recommend:** confirm the defaults.
4. **Milestone excitement** (`[excited]` on a win) — exit-code can't tell a merge
   from an `ls`. **Recommend:** accept its loss in v1.
