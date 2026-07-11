# OO Ratchet — Design for Improvements

Status: **FINALIZED** — peer-reviewed by `adb` (CI/operational) and `gvr`
(algorithm), operator-ratified. Ready for implementation (two PRs).
Scope: `tools/oo_score.py`, `.oo-baseline.json`/`.oo-audit.jsonl` lifecycle, CI.
Fixes concerns A1, B1, B3, C1, C2, D1, D2. `vox` first; propagate to the other
`oo_score.py`-consuming repos afterward.

## 1. Current model (ground truth)

- **`check()`** (`oo_score.py:573`) scores `git diff HEAD~1..HEAD` (`:492`) — the
  last commit only — and compares **current-tree metrics** against the baseline
  entry **in the current tree** (`:606`). Fails on any regression (`:640`); fails
  if no touched file improved ≥1 metric (`:672`); "trivial pass" when no `.py` is
  touched (`:594`). `main()` parses argv by substring membership (`:849`), not a
  real parser.
- **`update()`** (`:683`) is whole-tree and **refuses** any regressed file
  (`:722`) — it never loosens.
- **CI** (`lint.yml`, `test.yml`) runs ruff/mypy/pyright/shellcheck + pytest.
  **Neither runs `check-oo`/`check-coupling`.** The ratchet is enforced only by a
  developer's local `make check` + the per-edit hook.

Two root flaws: **R-A** the ratchet isn't enforced durably (no CI); **R-B**
`check()` compares code against the baseline *in the same tree*, so a change that
improves code and records the improvement together shows `current == baseline` →
"no improvement" and is penalised for locking in its own win.

## 2. The fix, in one sentence

Measure improvement as **code-at-HEAD vs. baseline-at-the-merge-base**, run that
check **in CI** folded into the required `lint` job against the merge-base, and
make `update` **scoped and symmetric** with `check` while preserving its
never-loosen invariant — then a PR improves code and locks in its baseline in one
commit, and the squash lands clean.

## 3. Solutions (with peer-review resolutions folded in)

### S1 — Enforce the ratchet in CI (fixes **A1**)

- **Fold two steps into the existing, already-required `lint` job** (not a new
  required context — a new context deadlocks every open PR and the introducing PR
  itself). Steps: `make check-oo` and `make check-coupling`.
- Checkout with **`fetch-depth: 0`**, then `git fetch origin ${{ github.base_ref }}`
  (PR) so the base is present and current before resolving it.
- **Portability:** CI calls `make check-oo` (each repo's `SRC` is encapsulated in
  its Makefile), and the Makefile grows an env-injected base flag so the workflow
  YAML is byte-identical across repos:

  ```make
  OO_BASE ?=
  check-oo:
  	uv run python tools/oo_score.py $(SRC) --check $(OO_BASE)
  ```

  CI exports `OO_BASE=--base-ref $(git merge-base origin/$BASE_REF HEAD) --require-base`
  on `pull_request`, and `--base-ref HEAD~1 --require-base` on `push:[main]`.
- **`push:[main]` run** too — a tripwire that catches a red `main` (bad squash /
  direct push) immediately. It detects, it cannot prevent — see S10 (concurrency)
  and the runbook (§5).
- **Never mutate in CI:** `--update/--reconcile/--relax/--rebaseline` refuse to
  run when `GITHUB_ACTIONS=true` unless an explicit `--allow-ci-write` is passed
  (closes the "CI rewrites the baseline to mask a regression" foot-gun).

### S2 — Compare against the **merge-base** baseline, not the in-tree baseline (fixes **B1, C1**)

`check()` gains `--base-ref REF`. Resolve **one** ref and use it for both the
baseline load and the touched-set:

```text
BASE=$(git merge-base origin/main HEAD)     # PR: the divergence point
# push:[main] after a squash: HEAD~1 IS the merge-base — pass it directly.
base_baseline = parse( git show "$BASE:.oo-baseline.json" )
touched       = py files in ( git diff -M "$BASE" )   # base vs work tree
current       = score the work tree                    # == HEAD in a clean checkout
```

For each touched file, compare `current[file]` against **`base_baseline[file]`**
(never the in-tree baseline). Worse-than-base → REGRESSED (subject to the S7
relax-waiver); strictly-better → counts toward the improvement gate. The PR
freely updates the in-tree `.oo-baseline.json` in the same commit; `check` never
reads it for the comparison, so `current == in-tree-baseline` is no longer a trap.

- **Why merge-base, not `origin/main` tip (gvr R1):** using the tip fails a PR
  for a file it never touched when `main` advances (the tip's baseline and the
  tip's tree both diverge from the PR's divergence point). The merge-base is the
  point the PR forked from — the correct "before."
- **Score the work tree; CI's clean checkout makes it == HEAD (gvr R7,
  accepted):** the base baseline is commit-derived (`git show`), but the touched
  set is `git diff <base>` (base vs work tree) and the current metrics score the
  work tree — not `base..HEAD`/HEAD. In CI the checkout is clean, so work tree ==
  HEAD and this is identical to scoring HEAD; locally it keeps `make check` and
  `make update-oo` reflecting a developer's tracked pre-commit edits, which
  `base..HEAD` would hide. Untracked-but-unstaged files fall outside `git diff`
  until added — expected, and in CI everything under review is committed.
- **Squash correctness (B1):** on `push:[main]`, `HEAD~1` is the squash's
  merge-base, so `keys_env 6 vs parent's 20 → IMPROVED` — the squash no longer
  erases the delta.
- **S3 is a precondition, not a convenience (gvr):** S2 only stays correct if
  `update` is scoped (S3) — a whole-tree update on a branch that diverged before
  `main` advanced would rewrite unrelated entries back to stale values and the
  squash would carry them as regressions.

### S3 — Symmetric scoped `update`; explicit whole-tree `--reconcile`; **never loosen** (fixes **B3**)

- `--update` becomes **scoped by default** to `git diff <BASE>..HEAD`, symmetric
  with `check`. It **retains `update()`'s per-metric refusal verbatim** (`:712`):
  it writes an entry only when every metric is better-or-equal to the current
  in-tree baseline; a regressed metric is refused with exit 1. Scoped update
  **cannot** loosen — loosening is `--relax` only (gvr R4).
- `--reconcile` (new, explicit) is the whole-tree sweep for intentional backlogs
  (the `voxd/programs` pass). It **also refuses per-metric regressions** — it
  adds/updates improvements and adds new files, but a genuine regression still
  goes one file at a time through `--relax`. `--reconcile` is not a blanket
  loosen (that is the "blanket rebaseline" the policy forbids).

### S4 — Require touched files to be locked in (completes **C1**)

`check` asserts: for every touched `.py` file, the **in-tree** baseline entry
equals the file's current metrics. A touched file that improved but wasn't
re-baselined → fail ("run `make update-oo`"). S2 makes improve-and-lock legal in
one commit, so `make update-oo` (scoped) satisfies this trivially. Net: every
merged PR leaves the baseline exactly matching the code for every file it touched.

### S5 — Close the new-file improvement loophole (fixes **C2**)

Membership is evaluated against the **base baseline** (gvr R5 — not the in-tree
baseline, which changes after `update` and would make the branch order-dependent):

- PR modifies ≥1 file **present in `base_baseline`** → the required ≥1-improvement
  must come from such an existing file (real paydown); new files must pass
  absolute thresholds but don't by themselves satisfy the gate.
- PR is a **pure addition** (touches no file in `base_baseline`) → new files
  clearing thresholds satisfy the gate. **(O1 ruled: pure-add PRs are NOT forced
  to also pay down existing debt.)**
- **Documented waiver:** a delete-only or doc-only PR yields `touched = {}` →
  trivial pass; the improvement gate is conditional on touching `.py`. State this
  so reviewers know (gvr R5). It is not exploitable — any PR that also modifies a
  `.py` re-arms the gate.

### S6 — Enforce baseline completeness (fixes **D1**)

- Completeness is checked against the **scorer's own enumerated file set** — the
  exact set `_score_directory` produces (its leading-`.` skip and its
  parse-error exclusion), **not** an independent `glob` (gvr R6). Otherwise a
  dotted or unparseable file is demanded in the baseline it can never have.
- **Scope split (adb F3):** PR `--check` enforces completeness **diff-scoped** —
  a new `.py` in the PR's diff must be added to the baseline (with S4, its entry
  == current). Whole-tree completeness runs only on `push:[main]` (and via an
  explicit `--audit-completeness`), so one untracked file on `main` doesn't red
  every unrelated PR.
- **Paths normalized repo-relative** everywhere (scorer keys, `git diff` paths,
  baseline keys) so the intersection can't silently empty under an absolute-path
  or `.` invocation (gvr R6 latent bug).

### S7 — First-class scoped relax-with-justification, reconciled with S2 (fixes **D2**)

- `--relax FILE --justify "REASON"` (single file, non-empty justification
  required): rescore `FILE`, write current values to the in-tree baseline **even
  if looser**, append an audit entry `verdict:"relaxed"` with old→new deltas and
  the reason. Replaces hand-edits.
- **Reconciles the S2 contradiction (adb F2 / gvr R2 — the design's one internal
  inconsistency):** `check` **waives** the vs-base REGRESSED verdict for a
  `file+metric` **iff** (a) the in-tree baseline entry for it equals current
  (locked, S4) **and** (b) the current change's `.oo-audit.jsonl` carries a
  matching `verdict:"relaxed"` entry for that exact `file+metric`. Without this,
  the sanctioned relax commit fails its own CI — D2 would be unfixed for the PR
  flow. A relax-only change is also exempt from the "no improvement" gate.
- **`--justify` is an audit marker, not an enforcement gate** (gvr): any
  non-empty string passes; the real control is the human-reviewed, greppable
  audit entry plus the visible baseline diff in the PR. Documented as such — not
  sold as prevention.

### S8 — Renames carry their history (new; fixes gvr R3 — rename laundering)

`git diff` reports a pure rename as the **new path only**, so today a renamed
file misses its old baseline key, hits the "new file, absolute-thresholds-only"
branch (`:608`), and its history (e.g. a CC-40 god-function) is erased and may
pass. `check`/`update` must consult `git diff --name-status -M`: for an `R`
(rename), map new→old, **carry the old `base_baseline` entry to the new path**,
and compare the current metrics against it. Completeness treats the old path as
removed and the new path as its continuation.

### S9 — Audit log records merged reality, not PR branches (fixes concurrency + E2)

**(Ruled: append on `push:[main]` only.)** Stop appending `.oo-audit.jsonl` in PR
branches — two concurrent PRs both appending at EOF guarantee a git conflict on a
tracked file. Instead, a `push:[main]` step (with `--allow-ci-write`) appends the
audit entry for what actually landed, referencing the **PR/bead** (`"source":
"vox-djua #308"`) rather than a pre-squash hash that doesn't survive (E2). The
per-PR record is the visible `.oo-baseline.json` diff + the PR itself.

### S10 — Concurrency: require up-to-date branches (fixes adb F1)

**(Ruled: require up-to-date branches.)** Two in-flight PRs touching the same file
each measure against a stale merge-base; both pass, `main` ends red, and the
`push:[main]` tripwire only detects it. Enable branch-protection **"Require
branches to be up to date before merging,"** which forces the second PR to rebase
onto post-merge `main` and re-run (now comparing against the updated baseline) →
blocked pre-merge. Cost: serialises merges + extra CI re-runs (acceptable — `vox`
is low-volume). The `push:[main]` tripwire + the red-main runbook (§5) stay as
defence in depth.

## 4. Concern → solution matrix

| Concern | Solution |
|---|---|
| A1 CI doesn't run the ratchet | S1 (folded into `lint`, `make check-oo` + `OO_BASE`) |
| B1 squash-merge hole | S2 (merge-base baseline compare) |
| B3 check-scoped / update-whole-tree | S3 (scoped symmetric update + `--reconcile`, never-loosen) |
| C1 lock-in dance | S2 + S4 |
| C2 new-file loophole | S5 (membership vs base baseline; O1 = no pure-add paydown) |
| D1 no completeness enforcement | S6 (scorer file set; diff-scoped in PR, whole-tree on main) |
| D2 stale-baseline deadlock | S7 (`--relax --justify` + the S2 relax-waiver) |
| (concurrency, surfaced in review) | S10 (require up-to-date) + `push:main` tripwire |
| (rename laundering, gvr) | S8 |
| (audit conflicts + E2) | S9 (append on push:main) |

## 5. Rollout (two PRs — ruled) + runbook

**Implementation prerequisite (adb F6):** `main()` must move from substring
argv-sniffing to **argparse** (positional `SRC` + typed `--base-ref`, `--relax`,
`--justify`, `--reconcile`, `--require-base`, `--allow-ci-write`,
`--audit-completeness`) before any value-flag lands.

- **PR 1 — tooling** (`tools/oo_score.py`, Makefile `OO_BASE`): argparse,
  merge-base base-compare (S2), scoped `update` + `--reconcile` with never-loosen
  (S3), lock-in + diff-scoped completeness assertions (S4, S6), `--relax` + the
  relax-waiver (S7), rename handling (S8), path normalization, CI-write guard.
  Provable entirely by local `make check` + unit tests before any CI gates on it.
- **PR 2 — CI + policy**: fold `check-oo`/`check-coupling` into `lint` with
  `fetch-depth: 0` + fetch + `OO_BASE` (S1); the `push:[main]` job + audit append
  (S9); enable branch-protection "require up-to-date branches" (S10). Land only
  after PR 1 is green on real PRs.
- **Red-main runbook (adb F8):** the `push:[main]` job failing means `main` is
  red. Response: (1) it pages the on-call agent/operator; (2) revert the
  offending squash *or* open a fix-forward PR that re-improves the metric or, if
  the regression is accepted, uses `--relax --justify`; (3) the follow-up PR
  passes via the normal S2/S7 path. Owner: the merging agent.
- **Cross-repo (ruled: vox first):** land + prove in `vox`, then `adb` propagates
  the canonical `oo_score.py` + the `lint`-job change to the other consuming
  repos in dependency order, each with a baseline-bootstrap step
  (`--reconcile`) and the base-absent fallback (S1) so no introducing PR
  self-deadlocks.

## 6. Resolved decisions

- **F1 concurrency** → require up-to-date branches + tripwire + runbook (S10).
- **Cross-repo scope** → vox first, propagate after (§5).
- **Audit log** → append on `push:[main]` only (S9).
- **PR split** → two PRs, tooling then CI (§5).
- **O1 (pure-add paydown)** → no; pure additions aren't forced to pay down (S5).
- **O2 (base fallback)** → hard-fail in CI via `--require-base` on an
  *unresolvable* base; fall back to trivial-pass only when there is *no base AND
  no prior baseline* (first-adoption bootstrap) so the introducing PR passes
  (adb F4 / gvr O2).
