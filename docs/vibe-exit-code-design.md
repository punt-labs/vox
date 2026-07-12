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

```
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
- **Chimes.** Chimes currently vary by signal *type* (a per-signal chime map).
  Collapsing to `ok/fail` means chimes key on `ok/fail` + the mood pitch-shift —
  fewer distinct tones. **Recommended:** accept the collapse; the mood pitch
  still carries expressiveness. (Open decision — see below.)

## What gets deleted

- `command_signal.py`'s success/failure marker tables and the pytest/ruff/git
  regexes.
- The typed signal vocabulary and its `signal_names()` surface.
- The per-signal chime map, replaced by an `ok/fail` (+ mood-pitch) mapping.

Forward integration only — the old classifier is removed in the same change, not
shimmed (PL-PP-1).

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

## Open decisions for this review

1. **Thresholds** — the model confirms `(frustFrom=3, wearyFrom=5, recentK=3,
   maxWindow=20)` sound and `focusFrom=1` forced. Confirm the defaults or retune
   the three free ones.
2. **Chimes** — collapse to `ok/fail` + mood pitch (recommended), or keep a
   light per-type flavor.
3. **Milestone excitement** — accept its loss in v1 (recommended), or scope a
   thin push/pr hint.
