# Development Workflow

All work in this repository runs as three nested loops. The outermost loop
owns the backlog: what is worth doing and in what order. The middle loop owns
one pull request: a single shippable, revertible change. The innermost loop
owns one mission: a single delegated piece of work inside that change.

```text
Level 1 — Backlog loop   one iteration = one work batch     (Linear)
  Level 2 — PR loop      one iteration = one pull request
    Level 3 — Mission    one iteration = one delegated mission
                         (design, code, test — a do-while)
```

Each level hands work down and receives finished results back. Escalation of
scope only goes up: a mission that uncovers a bigger problem grows its PR; a
PR that uncovers a new line of work files a Linear issue for the backlog loop. Defects
move the other way — anything found while a PR is open is fixed in that PR,
never sent back to the backlog.

The pseudocode at each level gives the control flow; a small Z schema at each
level gives the doorway conditions — what must be true to enter an iteration
and what must be true to leave it. Level 1 and Level 3 are in this file;
Level 2's are in [`WORKFLOW-PR-LOOP.md`](WORKFLOW-PR-LOOP.md), and its
`EnterPR`/`ExitPR` read the state defined here. The state those schemas
observe:

```text
LoopState
  signals       : ℙ SIGNAL  -- issues, alerts, messages not yet triaged
  open          : ℙ ISSUE   -- open issues: the single work funnel
  validated     : ℙ ISSUE   -- re-proven against current main
  claimed       : ℙ ISSUE   -- the current batch
  closed        : ℙ ISSUE   -- issues completed by a merged PR
  activeWorkers : ℙ WORKER  -- sub-agents editing the shared worktree
  testCount     : ℕ         -- tests collected by the suite
  ------------------------------------------------------------------
  validated ⊆ open
  claimed   ⊆ validated
  open ∩ closed = ∅         -- an issue is open or closed, never both
```

An issue's lifecycle is a walk through these sets: `open → validated → claimed
→ closed`. Steps that were performed ("the recap was sent") appear as named
predicates over declared terms, never as bare primed flags.

## Roles

The operator owns requirements and design direction, rules on genuine design
forks, and confirms the audio demo by ear. Claude runs everything else: the
backlog, the missions, the review cycles, and the merges. Review of code is
done by agents — the local review agents, plus Qodo and Devin Review on
the PR.
There is no human review gate. The operator reads code in the IDE, but that
inspection is separate, feeds design discussion only, and nothing in these
loops waits on it.

## Quality-check stages (DES-085)

Edits get advisory Ruff and shellcheck feedback. Each commit must pass the
commit hook: the existing `.githooks/pre-commit` gates (staged Ruff, mypy,
pyright and changed-module tests), chained to the Ethos commit-stage checks
in `.punt-labs/ethos/checks/vox-python.yaml` (staged Ruff check/format-check
and shellcheck, plus the OO, coupling and suppression ratchets when their
file scopes match). Full `make check` runs at handoff or before opening a PR,
not before every commit.

For snapshot-enabled Ethos, `make handoff-runtime` installs the version pinned
by this checkout on Linux x86-64 or Apple Silicon with macOS 15 or newer.
The macOS minimum follows the release workflow's offline smoke runner.
Run it once on a new machine and again when the pin changes. It needs Python 3
and curl, and checks the archive's SHA-256 before installing it under
`~/.punt-labs/ethos/runtimes/`. The gate never downloads it.

To refresh the pins after a toolchain or dependency change, dispatch **Handoff
runtime** on `main` with the next `handoff-runtime-vN` tag. Use the release
asset URLs and their companion SHA-256 files to update the profile. Do not
derive a pin by rebuilding locally: the archive headers are normalized, but
package-manager caches can record fetch metadata.
Keep the sidecar in JSON syntax (valid YAML) so the installer can read it
using the standard library, with no download before it verifies the runtime.
For local builds, use the upstream Node and ShellCheck distributions with
their license files intact. The builder also fetches the notices for the
selected uv release.

## Level 1 — The backlog loop

The backlog loop keeps the Linear backlog true and decides what to work on
next. Linear is the single funnel: every piece of work, whatever its origin,
is a Linear issue before it is anything else. One iteration selects and completes one
batch of work. The loop runs at session start and again whenever the current
batch is done.

```text
function backlog_loop():

    # 1. INTAKE — every signal becomes a Linear issue or is disposed at the door
    for signal in [github_issues, dependabot_alerts, biff_messages,
                   operator_requests, new_scope_found_while_working]:
                   # a defect inside an open PR's unit is fixed in that PR,
                   # never filed — only genuinely NEW scope arrives here
        if duplicate(signal):  close_at_door(signal, link_existing_issue)
        elif invalid(signal):  close_at_door(signal, stated_reason)
        else:                  bd_create(signal, labels, link_back_to_source)

    # 2. VALIDATE — an issue must be true before it is workable
    for issue in candidates(linear_ready):
        confirm it is still real against current main
        confirm nothing merged or decided has superseded it
        confirm it is one rollback-coherent unit (split or merge if not)
        confirm its blocked-by links reflect reality
        otherwise: fix the issue, or close it with the reason

    # 3. ASSESS — automatic ordering; escalate only on a genuine fork
    order = sort(validated, by:
        security severity                  # open HIGH/CRITICAL first, always
        > broken user journeys             # audio paths users actually hit
        > active epic continuity           # finish what is started
        > debt that blocks throughput      # decomposition, OO ratchet paydown
        > features)                        # then features
    if the ordering hits a fork the charter cannot resolve:
        ask the operator for a focus ruling
    else:
        proceed without asking

    # 4. SELECT AND EXECUTE
    batch = claim a realistic set from the top of the order
    for unit in rollback_coherent_units(batch):
        pr_loop(unit)                      # Level 2

    # 5. CLOSE OUT
    close GitHub issues resolved by the merged PRs, linking each PR
    send the batch recap via beadle-email  # to jim@punt-labs.com; covers what
                                           # the per-merge recaps do not: intake
                                           # dispositions, issues closed at
                                           # validation, order changes
    return to intake — new signals have accrued while working
```

Entry and exit for one batch iteration:

```text
EnterBatch ≙ [ LoopState ]                -- no precondition: the backlog loop
                                          -- is a do-while(true); intake always
                                          -- has standing to run

ExitBatch ≙ [ Δ LoopState |
  intakeDisposed(signals)                 -- every signal observed at this
                                          --   iteration's intake became an issue
                                          --   or was closed at the door with a
                                          --   reason; new signals keep accruing,
                                          --   so the live queue is never empty
  ∧ claimed′ = ∅ ∧ claimed ⊆ closed′      -- the batch drained: every claimed
                                          --   issue closed by a merged PR
  ∧ resolvedIssuesClosed(mergedPRs)       -- GitHub issues answered with their PR
  ∧ batchRecapSent(claimed) ]
```

### Intake

Work arrives from five places: GitHub issues, Dependabot security alerts,
biff messages from agents in other repositories, requests from the operator,
and new lines of work discovered while building. That last source carries a
boundary: a defect found inside the unit of an open PR is fixed in that PR and
never becomes an issue; only genuinely new scope — discovered outside any open
PR, or clearly a separate rollback unit — enters at intake. At intake, each
signal is either turned into a Linear issue or closed at the door with a stated reason
— duplicate of an existing issue, or invalid.

Security alerts map severity to priority: a critical or high alert becomes a
Urgent issue and goes to the front of the order. Security work does not wait.

Intake must not depend on remembering to look. A recurring poll (via the
`/loop` skill) or a session-start sweep checks the GitHub issue list and the
Dependabot alert list, so an alert filed overnight is an issue by the time the
first batch is selected. Start every session with `/loop 2m /biff:read` so
messages from other repos surface too.

### Validation

The codebase moves; issues rot. Before an issue is workable, confirm it is still
real: reproduce the bug or re-check the premise against current main, confirm
no merged PR or design decision has superseded it, confirm it describes one
rollback-coherent unit, and confirm its dependency links are correct. A stale
issue is closed with the reason. A bloated issue is split. Validation covers the
candidates for the coming batch, not the whole backlog every time.

### Assessment

Ordering is automatic in the steady state. The sort is:

1. **Security severity.** Open high or critical alerts outrank everything.
2. **Broken user journeys.** A bug on an audio path users actually hit — a
   provider that will not authenticate, a hook that plays the wrong chime, a
   record that never writes its file — beats new work.
3. **Active epic continuity.** An epic in flight keeps its claim until it is
   done. Interleaving epics is how a backlog churns without shipping.
4. **Debt that blocks throughput.** OO-ratchet paydown, module decomposition,
   test infrastructure that makes the next ten changes cheaper.
5. **Features.** New providers, voices, music, applet surface.

The operator is asked for a focus ruling only when the ordering hits a genuine
fork the rules cannot resolve: two epics competing to be active, a strategic
pivot, or a shift in the debt-versus-feature balance. Direction belongs to the
operator; sequencing inside a settled direction does not require asking.

## Level 2 — The PR loop

One iteration of the PR loop produces one merged pull request. It is entered
when the backlog loop hands down a unit of work sized for throughput, and it
runs the missions (Level 3) needed to build that unit.

### Sizing: throughput, not purity

Nobody reads these diffs. Agents review them, and they squash-merge. So the
size of a PR is an economic decision, not a hygiene one:

- **The floor is transaction cost.** Every PR pays a roughly fixed overhead —
  branch, full-diff review rounds, the audio demo, CI, remote review cycles,
  the merge gate, the recap. A PR too small pays that overhead for too little.
- **The ceiling is reviewer effectiveness.** The reviewers are agents, and
  past a certain diff size their quality drops. A PR too large buys throughput
  at the cost of review quality.
- **The typical right size** is several small issues batched together, or one
  coherent slice of a larger issue. An issue too large to slice into one PR is
  mis-filed: decompose it into an epic whose child issues fit the band.
- **Rollback coherence still binds.** Whatever merges must revert together
  sensibly. That is the one structural constraint. "Purity," "one concern per
  PR," and "keep the diff small" are not criteria — a docs fix, an OO-ratchet
  paydown, or an adjacent bug fix riding along is welcome, and an improvement
  is never held back or split out for tidiness.

The control flow, the terms it reads from GitHub (`round`, `qodo_in`,
`pending`, `answered(f)`, `audible(pr)`, `demo_ok`,
`reviewed_head`, and the rest), `merge_gate`, `close_out`, `local_review`, and
the `EnterPR`/`ExitPR` Z schemas are specified in
[`WORKFLOW-PR-LOOP.md`](WORKFLOW-PR-LOOP.md). It is the authoritative text for
every PR-loop step and is **not** auto-loaded: read it
before starting a unit of work (step A holds the design-ratification gate for
architectural units), running a local review or pushing a fix, opening a PR,
driving remote review, running the demo, or deciding a merge. The summary that follows (local review, remote review, the demo gate)
stays here.

### Local review before the PR

Full-diff local review is where issues die cheaply. `code-reviewer`,
`silent-failure-hunter`, and `qodo review` run on the full diff —
silent-failure-hunter matters especially in vox, where a swallowed provider
error becomes silence the user cannot diagnose, and `qodo review` is the same
engine that reviews the PR, so what it finds locally does not come back as a
PR round. Local rounds repeat until a round leaves nothing to fix: at most
four fix rounds plus one review that verifies the last fixes. Every fix diff
goes through the same loop before it is pushed, PR-finding fixes included.
Every finding is fixed now or dismissed with the exact finding, the specific
reason, and the code reference — nothing deferred to an issue. This loop
replaces the "re-run until zero findings" sequence in the deposited
`.punt-labs/ethos/CLAUDE.md` guide.

From the second round on, the review is scoped to what the diff changed:
regressions it introduces and Highs inside it — without that boundary a
failure-focused reviewer walks outward into untouched code and the rounds do
not converge. The three reviewers are not substitutes (each finds defects the
others miss), so every full diff and every fix diff of 20 lines or more gets
all three. A fix diff under 20 lines gets `qodo review` alone, and `djb`
joins any diff touching its domain whatever its size.

### Remote review: Qodo and Devin Review, at most two rounds

Qodo is the primary remote reviewer (`.pr_agent.toml`). It reviews when the
PR opens and never on push. High findings post inline as threads, and the
`required_review_thread_resolution` rule in ruleset 13252005 makes each one
block the merge. Medium findings appear in the summary; every Qodo finding is
closed through the Qodo CLI (`mark-implemented` naming the fix sha, or
`dismiss`), never a ledger line, except one reopened by a force-push or rebase
(`answered(f)`). Lows are dropped by `comments_routing`.

Devin Review also reviews each PR. Its findings are handled whenever they
arrive. The Cursor security automation ("Find vulnerabilities") also runs
here; its threads are findings like any other.

Copilot is on a trial (operator ruling, 2026-10-23): one review, requested at
open, on 5 vox PRs after #526, none on push. The trial counts how many real
bugs Copilot alone finds on top of this local loop; the operator keeps it only
if that count is not zero. Its findings are findings like any other.

Round 1 is the review at open. Round 2 — one `/agentic_review` — happens only when round 1's fixes included a High, and it is
the last. A new High in round 2 goes to the operator.

This holds only when three settings are in place: `.pr_agent.toml` is on `main`
(Qodo reads it from the default branch); the `copilot_code_review` rule is gone
from ruleset 13252005 (so Copilot reviews only when requested); and Qodo's
"Review every push" is Disabled in the portal.

### The audio demo gate

`make check` passing means the code compiles and the tests pass. It does not
mean the feature works — and for vox it cannot, because vox produces audio
that only a human can judge. Before the PR merges, the feature is driven
through its real entry point — a `mic` tool call, a CLI command, a hook fire —
with the expected outcome written down first, after `make install` and
`vox daemon restart` so the running daemon is the just-built code. Machine
evidence comes from `mic:status`, `vox doctor`, and a grep of `vox.log`. For
an audible PR, the operator then confirms what no introspection API can attest: that the audio
sounded right — clean, the correct voice, the music ducked, the chime fired.
Run the canonical flight in [`docs/testing/manual-tests.md`](testing/manual-tests.md)
and ask after each audible step, immediately, because audio impressions decay
within seconds. This is the one human gate in the loops, and it is a demo, not
a diff.

The ear is asked only for what a machine cannot judge. A PR that plays nothing
(`audible(pr)` is false: a status reading, `vox doctor`, a refusal) is demoed
by the leader's own run of the real entry point, with the command and output
pasted into a "demo confirmed (not audible)" comment; the operator is not
asked to run or bless it, and when the operator does say to run the command,
that is the ruling. An audible PR is verified first by the roundtrip tier,
which synthesizes a fixed corpus through the providers, transcribes it back and
checks the word error rate, proving the synthesis path still speaks
intelligibly; the operator then confirms only what that corpus cannot: that the
demo step said the right words, in the right voice, with the delivery, the
ducking, the chime and its timing. A finding that surfaces after
the merge is recorded on the PR with the leader's reasoning, by its source's
path in `answered(f)` (a Qodo finding through the Qodo CLI, any other by a
ledger comment), and the next unit starts; it is never a blocking question, and the two-minute rule in the
workspace `CLAUDE.md` applies to it as to any other.

## Level 3 — The mission loop (design, code, test)

One iteration is one delegated mission: a single piece of design,
implementation, test, or review work executed by a specialist sub-agent under
an ethos mission contract. The next mission does not start until this loop
completes on the current one.

The mission loop is a do-while: the work runs at least once, and the
review-and-fix cycle repeats until a round comes back clean.

```text
function mission_loop(mission):
    contract = write_contract(mission)   # problem, invariants, quality bar,
                                         # commit discipline — never a
                                         # write-set for design work; cite the
                                         # OO rules with an example for
                                         # protocol/data work
    dispatch(contract)                   # mission create + background spawn;
                                         # verify the worker is running
    do:
        worker designs / codes / tests   # tests lead; TDD for bug fixes;
                                         # stateful-audio work is model-checked
                                         # BEFORE implementation
        worker commits locally           # each commit passes the commit hook
        make check                       # full gate at worker handoff
        result   = worker submits
        findings = evaluator review      # a DISTINCT specialist
                 + leader verification   # make install; vox daemon restart;
                                         # make test; make test-e2e against the installed wheel;
                                         # drive the real entry point;
                                         # review agents on the mission diff
        if findings: reflect(findings)   # another round, same mission
    while findings remain
    close(mission)
```

Entry and exit for one mission iteration:

```text
EnterMission ≙ [ LoopState; m : MISSION |
  contracted(m)                           -- problem, invariants, quality bar
  ∧ workerRunning(m)                      -- dispatch is two operations; a
                                          --   contract alone is orphaned work
  ∧ activeWorkers = {workerOf(m)}         -- one worker at a time in a shared
                                          --   worktree
  ∧ (statefulAudioClass(m) ⇒ modelChecked(m)) ]  -- z-spec before code

ExitMission ≙ [ Δ LoopState; m : MISSION |
  verdict(m) = accept                     -- from an evaluator ≠ worker
  ∧ findings(m) = ∅                       -- the do-while ran dry
  ∧ (∀ c : commitsOf(m) • commitChecksGreen(c))  -- commit hook passed
  ∧ fullGateGreen(m)                      -- make check passed at handoff
  ∧ testCount′ ≥ testCount                -- coverage never decreases
  ∧ ooRatchetHeld(m)                      -- no metric regressed; ≥ 1 improved
  ∧ missionClosed(m) ]
```

`statefulAudioClass(m)` holds when the mission changes a stateful subsystem
with three or more modes and transitions between them, or an invariant that
must hold across transitions — the music playlist (`off → generating-first →
playing-filling → rotating`), the vibe/signal accumulation, the daemon
playback lifecycle, a concurrent-writer discipline (the `vox.log` rotation
lock). It does NOT hold for pure I/O helpers, provider SDK wrappers, text
normalization, or single-function fixes with no state. When it holds, the
model (`docs/<feature>.tex`) is `fuzz`-clean and, for higher-stakes
invariants, ProB-model-checked, BEFORE the implementation mission dispatches.

### Who does what

The leader runs the workflow; the specialists produce the work. The boundary
is strict in both directions.

**The leader owns the workflow.** The backlog, the mission contract,
dispatch, monitoring, the local review agents (run on each mission's diff and
on the full diff), the audio demo, and every git and GitHub operation:
branches, pushes, opening the PR, driving remote review, resolving threads,
merging, and closing out. The leader does not write production code. The only
files the leader authors directly are the doc set — `CHANGELOG.md`,
`CLAUDE.md`, `DESIGN.md`, `README.md`, this `WORKFLOW.md` and `WORKFLOW-PR-LOOP.md`, design docs, and
plan files — and those still ship through PRs.

**The worker owns the work.** The thinking and the code inside its mission —
the design decisions its contract leaves open, the tests, the implementation,
and local commits on the current branch. A worker never creates branches,
never pushes, never opens PRs, and never touches review threads. Putting
workflow operations into a worker's prompt is a contract defect.

**The evaluator** — always a different specialist from the worker, and never
bound to the same role — reviews the worker's result inside the mission before
the leader accepts it. The pairings live in `CLAUDE.md`.

**Remote review findings are the one delegation that is not a mission.** When
Qodo or Devin Review reports on the PR, the leader reads each finding and hands
the mechanical fix to a bare `Agent()`, then pushes and resolves the thread
itself.

### The lifecycle

1. **Contract.** The leader writes the mission contract: the problem, the
   invariants, the quality bar, and the commit discipline — one commit per
   logical step, each passing the commit hook, never more than thirty minutes of
   work uncommitted. A design mission's contract never prescribes a write-set;
   the specialist decides what to create, split, or extract. For protocol or
   data work the contract cites the OO rules (`../.claude/rules/python-*.md`)
   with a BEFORE/AFTER example, because sub-agents revert to procedural habits
   when the prompt is not explicit.
2. **Dispatch is two operations.** `ethos mission create` writes the contract;
   a separate `Agent(subagent_type=<worker>, run_in_background=true)` spawn
   starts the worker. Verify the worker is actually running — a contract with
   no agent behind it is orphaned work.
3. **The worker executes.** Tests lead: a bug fix starts from a failing test
   that reproduces the defect, and the fix is done when it passes; a feature
   ships its tests with the code; the test count never goes down. Stateful-
   audio work gets its model-check before implementation. Every commit passes
   the commit hook; full `make check` passes at handoff — zero exceptions,
   zero unauthorized suppressions.
4. **The leader monitors by the filesystem, never by git activity.** A worker
   editing files is working, even with zero commits — analysis and reading are
   invisible. Progress is judged by whether the working tree is changing and
   advancing. An empty commit log is never a reason to intervene, commit by
   proxy, or stop the agent. A genuine stall — no file changes over a long
   window and no response to a status message — is the only cause for taking
   over. Monitoring is silent: concrete facts or action, no narration of the
   wait.
5. **Result and evaluation.** The worker submits its result; a distinct
   evaluator reviews it; reflect-and-advance rounds continue until the
   evaluator accepts.
6. **The leader verifies and closes.** Check the result against the contract:
   `make install`, `vox daemon restart`, `make test`, plus `make test-e2e` against the installed
   wheel, and exercise the change through its real entry point with the
   expected output written first — one invalid input, one missing/erroring
   dependency, one boundary. Run the applicable local review agents on the
   mission diff and fix every finding; a dismissal requires the exact finding,
   the specific reason it does not apply, and the code reference. If the result
   raises a design question, it goes to the operator as a concrete decision —
   with a recommendation — before any dependent mission dispatches. Then close
   the mission.

## Invariants

1. **Linear is the single funnel.** Every piece of work is a Linear issue before it
   is anything else; external queues (issues, alerts) drain into it at intake.
2. **A satisfied merge gate means merge now.** The gate is the only path to a
   merge, and nothing waits once it passes. Merge is always explicit — never
   `--auto`, never `--admin`.
3. **Do or do not.** A finding is fixed in this PR or dismissed with a
   reason, the moment it arrives. It is never deferred to an issue or a
   follow-up PR. A thread is resolved only once it is answered per
   `answered(f)` — a Qodo finding closed through the Qodo CLI
   (`mark-implemented` or `dismiss`), any other finding by a landed commit
   or a ledger reason — one by one, never in bulk.
4. **A push restarts CI, not review.** Local rounds run until clean (at most
   four fix rounds plus a verifying review) before the PR opens and before
   every fix push. PR rounds are at
   most two, and the second only after a High fix.
5. **Defects flow inward, scope flows outward.** A defect found while a PR is
   open is fixed in that PR. Only genuinely new lines of work become issues;
   a review finding is never new work.
6. **The operator gates what only an ear can judge, and direction; never
   diffs, sequencing, or paperwork.** A PR that plays nothing is confirmed by
   the leader's own recorded run; an audible PR by the roundtrip tier and
   then the operator's ear for voice, delivery, ducking and chimes. A gap a
   hook or reviewer finds after the merge is recorded, not asked.
7. **Close-out is inside the loop.** The recap email, branch hygiene, and
   starting the next unit are steps of the loop, not afterthoughts.
8. **No migration, compat, shim, or version-bridge code.** Punt Labs products
   have no installed base to migrate; the leader strikes any such element at
   design review, before it reaches implementation.
9. **The daemon is the audio host.** `voxd` owns playback, the cache, the
   music program, and the recordings on disk; clients are thin controllers.
   Work that touches audio, audio files, or daemon-owned state routes through
   the daemon so it behaves the same whether the daemon is local or remote.
10. **Never read or edit the content of `vox.md` / `vox.local.md`
    directly.** The daemon writes them; all state is reachable through the
    `mic` tools. Racing the files corrupts state. Read-only git (`git status`,
    `git diff`) is fine. The one sanctioned rewrite is the release step: when
    `punt release run` preflight reports `vox.md` dirty, run
    `git stash push -- .punt-labs/vox/vox.md`, release, then
    `git stash pop --index`, with vox sessions stopped first (a daemon write
    between push and pop is lost or must be redone).
