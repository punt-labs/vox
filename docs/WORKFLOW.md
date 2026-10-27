# Development Workflow

All work in this repository runs as three nested loops. The outermost loop
owns the backlog: what is worth doing and in what order. The middle loop owns
one pull request: a single shippable, revertible change. The innermost loop
owns one mission: a single delegated piece of work inside that change.

```text
Level 1 — Backlog loop   one iteration = one work batch     (beads)
  Level 2 — PR loop      one iteration = one pull request
    Level 3 — Mission    one iteration = one delegated mission
                         (design, code, test — a do-while)
```

Each level hands work down and receives finished results back. Escalation of
scope only goes up: a mission that uncovers a bigger problem grows its PR; a
PR that uncovers a new line of work files a bead for the backlog loop. Defects
move the other way — anything found while a PR is open is fixed in that PR,
never sent back to the backlog.

The pseudocode at each level gives the control flow; a small Z schema at each
level gives the doorway conditions — what must be true to enter an iteration
and what must be true to leave it. The state those schemas observe:

```text
LoopState
  signals       : ℙ SIGNAL  -- issues, alerts, messages not yet triaged
  open          : ℙ BEAD    -- open beads: the single work funnel
  validated     : ℙ BEAD    -- re-proven against current main
  claimed       : ℙ BEAD    -- the current batch
  closed        : ℙ BEAD    -- beads completed by a merged PR
  activeWorkers : ℙ WORKER  -- sub-agents editing the shared worktree
  testCount     : ℕ         -- tests collected by the suite
  ------------------------------------------------------------------
  validated ⊆ open
  claimed   ⊆ validated
  open ∩ closed = ∅         -- a bead is open or closed, never both
```

A bead's lifecycle is a walk through these sets: `open → validated → claimed
→ closed`. Steps that were performed ("the recap was sent") appear as named
predicates over declared terms, never as bare primed flags.

## Roles

The operator owns requirements and design direction, rules on genuine design
forks, and confirms the audio demo by ear. Claude runs everything else: the
backlog, the missions, the review cycles, and the merges. Review of code is
done by agents — the local review agents, plus Qodo and Bugbot (one run per
PR round) on the PR.
There is no human review gate. The operator reads code in the IDE, but that
inspection is separate, feeds design discussion only, and nothing in these
loops waits on it.

## Level 1 — The backlog loop

The backlog loop keeps the bead tracker true and decides what to work on
next. Beads are the single funnel: every piece of work, whatever its origin,
is a bead before it is anything else. One iteration selects and completes one
batch of work. The loop runs at session start and again whenever the current
batch is done.

```text
function backlog_loop():

    # 1. INTAKE — every signal becomes a bead or is disposed at the door
    for signal in [github_issues, dependabot_alerts, biff_messages,
                   operator_requests, new_scope_found_while_working]:
                   # a defect inside an open PR's unit is fixed in that PR,
                   # never filed — only genuinely NEW scope arrives here
        if duplicate(signal):  close_at_door(signal, link_existing_bead)
        elif invalid(signal):  close_at_door(signal, stated_reason)
        else:                  bd_create(signal, labels, link_back_to_source)

    # 2. VALIDATE — a bead must be true before it is workable
    for bead in candidates(bd_ready):
        confirm it is still real against current main
        confirm nothing merged or decided has superseded it
        confirm it is one rollback-coherent unit (split or merge if not)
        confirm its blocked-by links reflect reality
        otherwise: fix the bead, or close it with the reason

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
                                           # dispositions, beads closed at
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
                                          --   iteration's intake became a bead
                                          --   or was closed at the door with a
                                          --   reason; new signals keep accruing,
                                          --   so the live queue is never empty
  ∧ claimed′ = ∅ ∧ claimed ⊆ closed′      -- the batch drained: every claimed
                                          --   bead closed by a merged PR
  ∧ resolvedIssuesClosed(mergedPRs)       -- GitHub issues answered with their PR
  ∧ batchRecapSent(claimed) ]
```

### Intake

Work arrives from five places: GitHub issues, Dependabot security alerts,
biff messages from agents in other repositories, requests from the operator,
and new lines of work discovered while building. That last source carries a
boundary: a defect found inside the unit of an open PR is fixed in that PR and
never becomes a bead; only genuinely new scope — discovered outside any open
PR, or clearly a separate rollback unit — enters at intake. At intake, each
signal is either turned into a bead or closed at the door with a stated reason
— duplicate of an existing bead, or invalid.

Security alerts map severity to priority: a critical or high alert becomes a
P1 bead and goes to the front of the order. Security work does not wait.

Intake must not depend on remembering to look. A recurring poll (via the
`/loop` skill) or a session-start sweep checks the GitHub issue list and the
Dependabot alert list, so an alert filed overnight is a bead by the time the
first batch is selected. Start every session with `/loop 2m /biff:read` so
messages from other repos surface too.

### Validation

The codebase moves; beads rot. Before a bead is workable, confirm it is still
real: reproduce the bug or re-check the premise against current main, confirm
no merged PR or design decision has superseded it, confirm it describes one
rollback-coherent unit, and confirm its dependency links are correct. A stale
bead is closed with the reason. A bloated bead is split. Validation covers the
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
- **The typical right size** is several small beads batched together, or one
  coherent slice of a larger bead. A bead too large to slice into one PR is
  mis-filed: decompose it into an epic whose child beads fit the band.
- **Rollback coherence still binds.** Whatever merges must revert together
  sensibly. That is the one structural constraint. "Purity," "one concern per
  PR," and "keep the diff small" are not criteria — a docs fix, an OO-ratchet
  paydown, or an adjacent bug fix riding along is welcome, and an improvement
  is never held back or split out for tidiness.

```text
function pr_loop(unit):

    # A. BUILD — missions, one at a time
    branch from main
    if unit is architectural (daemon/client protocol, new provider,
                              hook-classification, stateful audio subsystem):
        design mission first, with no prescribed write-set
        leader reviews the design end-to-end and STRIKES any migration,
            compat, shim, or version-bridge element before it reaches code
        substantive issues go to the operator as concrete decisions
            (each with a recommendation), one ASK per issue
        implementation waits for the operator's ratification
    for mission in unit:
        mission_loop(mission)             # Level 3
        # bug fixes are TDD: a failing test reproduces the defect first
        # coverage rises with every mission
        # stateful-audio work is model-checked before implementation

    # B. FULL-DIFF VERIFICATION (local, before any PR exists)
    make check                            # full gate on the accumulated diff
    if local_review(full diff) is blocked: stop the unit until the
        operator rules                    # local_review: after close_out

    # C. SHIP — the PR opens before the demo, so remote review runs while
    #    the operator is away; the merge waits for the demo (merge_gate)
    push; create the PR (description carries the verification playbook)
        # Qodo reviews once, on open (.pr_agent.toml); Bugbot reviews once,
        #   on its own, at open (Cursor dashboard: once per PR) — no
        #   "bugbot run" comment for round 1
    if copilot_trial(pr): request a Copilot review   # once, at open; none
                                                      #   on push
    schedule a background poll with /loop 3m  # never gh pr checks --watch,
                                              # never foreground sleep loops

    # D. AUDIO DEMO — confirmed by ear, the one human gate. poll_tick is
    #    its only caller, so it runs at most once at a time, and it runs
    #    again after a non-exempt push or a failed demo

function demo(pr):
    no push to the branch, and no other demo, while this one runs
    if the operator is not available: return    # no comment; a later tick
                                                #   tries again
    from a clean worktree at the PR head H:
        make install; vox daemon restart  # the daemon serves old code until
                                          #   restarted; a stale daemon demos
                                          #   the old build
    write down the expected outcome, then drive the real entry point
        (mic tool, CLI command, hook fire)
    capture machine evidence: mic:status, vox doctor, and grep of vox.log
    run the canonical flight in docs/testing/manual-tests.md and ask the
        operator after EACH audible step — a log cannot judge whether the
        audio sounded right; only the operator's ear can
    if the operator confirms: comment "demo confirmed: H"   # demoed_head;
                                    #   only a PR that changes a non-exempt
                                    #   file reaches here (demo_ok is false)
    else: comment "demo failed: H" with what the operator heard; the
        failure is a finding like any other (fixed, local_review on the
        fix diff, pushed), and the next tick demos the new head

# Terms, all read from GitHub (Qodo's login is qodo-code-review[bot]).
# The rules are model-checked in punt-kit standards/pr-review-model.tex and
# pr-review-model-bugbot.tex.
#   round(pr)    = 1 + the number of "/agentic_review" comments the leader
#                  has posted on the PR
#   trigger(N)   = PR open for N = 1; the leader's (N-1)th
#                  "/agentic_review" comment for N > 1
#   head(N)      = the PR head commit when trigger(N) happened
#   qodo_in(N)   = `qodo read pr-review-session findings --pr-url <url>`
#                  reports review_session.status "completed" with a
#                  started_at at or after trigger(N) and a commit_sha that
#                  is head(N) or a later commit on the branch; or the
#                  outage fallback ran for round N. A session that started
#                  before trigger(N) — a round-1 session for N = 2 — never
#                  completes round N, and a clean review counts like any
#                  other.
#   bugbot_in(N) = a "Cursor Bugbot" check run on head(N) or a later branch
#                  commit, started at or after trigger(N) — the PR open for
#                  N = 1 (Bugbot's automatic run), round 2's "bugbot run"
#                  comment for N = 2 — and completed with a conclusion
#                  showing the review finished (success or neutral). A run
#                  that failed, timed out, or was cancelled reviewed
#                  nothing, so it does not count; a later push does not
#                  undo a completed run. A clean run posts no review, so
#                  read the check run. Or: 30 minutes have passed since
#                  trigger(N) (or since rerun(N), if later), bugbot_open is
#                  false, and, if a run for round N failed, rerun(N) was
#                  posted — the timeout covers an outage, never a live run
#                  and never a skipped re-run.
#   rerun(N)     = a "bugbot run" comment the leader posted because a
#                  round-N run ended without a finished review, plus a
#                  ledger comment `rerun(N): <failed check-run id>` posted
#                  in the same tick. The ledger line is what records the
#                  round: the comment text is the same for every round. One
#                  "bugbot run" comment may serve several rounds, one
#                  ledger line each. Round 2's trigger comment and a
#                  stuck-run nudge use the same text, have no ledger line,
#                  and are neither: a round has at most one rerun.
#   copilot_trial(pr) = not skipped(pr), and fewer than 5 vox PRs that are
#                  not skipped, numbered above #526 and below pr, carry a
#                  Copilot review request (a ReviewRequestedEvent for
#                  copilot-pull-request-reviewer, read from GitHub; #527
#                  was the first). A request counts toward the 5 whether
#                  or not a review came back.
#   copilot_in   = no Copilot review request exists on pr and not
#                  copilot_trial(pr), or a request exists and (a review by
#                  copilot-pull-request-reviewer exists or 30 minutes have
#                  passed since the request); a trial PR with no request is
#                  pending until the leader makes one
#   pending(pr)  = (not skipped(pr) and (not qodo_in(round(pr))
#                  or not bugbot_in(round(pr)))) or not copilot_in
#                  # a Copilot review someone requests on a skipped
#                  # (bot-authored) PR still counts
#   finding      = any unresolved review thread, from any author, or any
#                  Medium in Qodo's summary — whenever it arrives, late
#                  ones included
#   answered(f)  = one path per source, never both:
#                  - a finding with a Qodo id (from the CLI read): its
#                    attribution_status is closed (implemented, dismissed,
#                    outdated); close it with `qodo pr-review-session
#                    mark-implemented` (the explanation names the fix sha)
#                    or `dismiss` plus the reason and file:line. A ledger
#                    line does not close it, except a reopened one (below).
#                  - any other finding — Bugbot, Cursor security, Copilot,
#                    a human, the local outage fallback: fixed by a commit
#                    on the PR head, or a ledger comment gives the reason
#                    and file:line.
#                  Whatever the source, and whether or not it was
#                  escalated, a finding answered by a fix is answered only
#                  once its fix commit is on the PR head and passed
#                  local_review. A finding whose latest ruling is "fix" is
#                  answered only by its fix; a "dismiss" ruling, including
#                  one after a blocked fix, answers it once recorded.
#                  After a force-push or rebase, a mark-implemented finding
#                  is reopened, unanswered, only if the change its recorded
#                  fix sha made is no longer in the branch; new commit ids
#                  alone do not reopen it. A ledger line naming its Qodo id
#                  and a new fix sha answers it again, under the fix rule
#                  above, and that sha is its recorded fix.
#                  A finding repeated from an earlier round keeps its answer.
#   new High     = a High raised by a round-2 review (one that started at
#                  or after trigger(2)) and not raised in an earlier round;
#                  a manual review may re-scan the whole PR and repeat old
#                  findings, and a round-1 High that arrives late is not
#                  new — it takes the normal fix path
#   escalation   = the leader's "escalated:" comment on one new round-2 High,
#                  on a fix-ruled finding whose fix was blocked in
#                  local_review, on a blocked local_review, or on a stuck
#                  Bugbot run; closed by a
#                  later ledger entry recording the operator's ruling. The
#                  entry closes the escalation, not the finding (see
#                  answered(f))
#   skipped(pr)  = the PR author is a bot ([github_app] ignore_bot_pr in
#                  .pr_agent.toml); no title or label lets an author skip
#   reviewed_head = set by a completing Qodo review or outage fallback to
#                  the commit that review ran on (not the head when it
#                  completed), or by promoting a delta_head that is the PR
#                  head once every finding its delta review raised is
#                  answered. It never moves backward: a completing review
#                  moves it only when it is unset, is not in the head's
#                  history (a rebase orphaned it), or is an ancestor of the
#                  reviewed commit. A skipped (bot-authored) PR has none
#                  until a delta review promotes one
#   delta_head   = the PR head a local delta review ran on, set only when
#                  that review completes; unset until the first one
#                  completes
#   delta_base   = the markers among reviewed_head and delta_head that are
#                  set and are the head or an ancestor of it; empty when
#                  neither is — no marker yet, or a rebase orphaned both
#   delta range  = the PR's own commits in the head's history that no
#                  delta_base commit reaches:
#                  `git log <head> --not <delta_base> <base branch>`; with
#                  delta_base empty, `git log <head> --not <base branch>`,
#                  the merge-base(base branch, head)..head diff. Excluding
#                  the base branch keeps commits merged in from it out of
#                  every range
#   bugbot_open  = a Cursor Bugbot check run on any commit of the PR, not
#                  only the head, whose status is not completed (queued or
#                  in progress), whatever its age, and that has no recorded
#                  "proceed" ruling; only the run ending or a "proceed"
#                  ruling clears it, and a "wait" ruling does not
#   bugbot_stuck = a Bugbot run queued or in progress for more than 60
#                  minutes, with no recorded "proceed" ruling
#   demo_exempt(f) = f is under docs/, tests/, or .github/, is a ratchet
#                  file (.oo-*, .suppression-*), or is a top-level *.md,
#                  and is not under plugin/ — commands, skills, agents,
#                  and hooks there are behavior even when Markdown, because
#                  Claude Code executes them; src/, a dependency, or an
#                  install change can change what the operator hears
#   demoed_head  = the commit named in the leader's latest "demo confirmed:"
#                  comment, newer than any "demo failed:" comment
#   demo_ok(pr)  = every file the PR changes is demo_exempt, or demoed_head
#                  exists, is the head or an ancestor of it, and every file
#                  changed since it is demo_exempt
#   finding also = the leader's latest "demo failed:" comment, until a
#                  later "demo confirmed:" comment

function poll_tick(pr):
    state = current reviews, threads, checks   # prefix gh with
                                               #   env -u GH_TOKEN
                                               #   -u GITHUB_PERSONAL_ACCESS_TOKEN
    N = round(pr)
    if CI failed: fix in this PR
    if not skipped(pr) and not qodo_in(N)
            and 30 minutes have passed since trigger(N)
            and the fallback has not yet run for round N:
        run code_reviewer locally on the diff Qodo was asked to review;
            it completes round N for qodo_in only if it completed — on an
            error, re-run once, then report to the operator and stop;
            its findings have no Qodo id, so they take the ledger path
                                                   # outage fallback
    if a Bugbot run of any round M <= N completed without a finished
            review (failure, timeout, or cancelled — a fix push cancels an
            in-progress run) and no rerun(M) was posted:
        comment "bugbot run"; and, for each such M, a ledger comment
            "rerun(M): <failed check-run id>"
                                           # together rerun(M) for every such
                                           #   M: once per round, even after
                                           #   the 30-minute fallback
                                           #   elapsed, and for round 1 even
                                           #   after round 2 opened; it
                                           #   restarts the window; if the
                                           #   re-run fails too or none
                                           #   starts, the fallback in
                                           #   bugbot_in applies
    if a bugbot_stuck run has no "bugbot run" comment since it got stuck:
        comment "bugbot run"               # once; the leader cancels nothing
    if a run is still bugbot_stuck 30 minutes after that comment and no
            escalation has been posted for this run:
        post an escalation comment         # once, ever, per run; the operator
                                           #   rules: "proceed" — the run is
                                           #   abandoned, the ledger entry
                                           #   names its check-run id, and
                                           #   bugbot_open ignores it; "wait" —
                                           #   the escalation closes and
                                           #   bugbot_open stays true until
                                           #   the run ends or the operator
                                           #   later rules "proceed" on it,
                                           #   which needs no new escalation
    for each finding not answered(f) and not under an open escalation
            nor in a diff whose block escalation is open:
        if N == 2 and f is a new High with no ruling:
            post an escalation comment; the operator rules
        else:
            fix it now (answered after the push, below), or answer it by
                its source's path (answered(f))
            # never re-litigated; Qodo Lows are dropped by comments_routing;
            # no deferral beads; the latest ruling governs
    for each escalation the operator has ruled on:
        record the ruling in a ledger comment   # closes the escalation,
                                                #   not the finding
        a "dismiss" ruling answers the finding (a Qodo id: `dismiss`)
    if fixes were made: delegate them to a bare agent; make check; commit
        if local_review(the fix diff) is blocked:
            post an escalation comment naming the blocked fix commit and
                what blocked it, and one on each fix-ruled finding in the
                diff; every other finding in the diff stays unanswered
                and is taken again after the operator rules on the block:
                "retry" (the fix is reworked and passes local_review
                before any push) or "drop" (the blocked fix commit is
                discarded unpushed and its findings return unanswered);
                either ruling's ledger entry closes the escalation; stop
        push once                          # every fix reaches the PR head;
                                           #   never while demo(pr) runs
    answer each finding fixed this tick, or fix-ruled, whose fix commit is
        now on the PR head and passed local_review — a Qodo id by
        `mark-implemented` (a reopened one by a ledger line naming its
        Qodo id and the new fix sha), any other source by that commit,
        named in its ledger entry
        # whatever the source: answered earlier, a stopped fix would merge
        #   unfixed, with no open escalation and no unanswered finding
    resolve each thread that is answered, one by one — the leader
        resolves, not the agent
    if not skipped(pr) and not pending(pr) and N == 1
            and a High was fixed since trigger(1):
        comment "/agentic_review"; comment "bugbot run"   # opens round 2,
                                                          #   the last
    if not pending(pr) and no delta review is running
            and the PR head is neither reviewed_head nor delta_head:
        run code_reviewer locally on the delta range; its findings are
            findings like any other; when it completes, set delta_head to
            the head it ran on
                                         # an unset delta_head or a marker a
                                         #   rebase orphaned never blocks it
    if delta_head is the PR head, reviewed_head is not, and every finding
            that delta review raised is answered(f):
        set reviewed_head to delta_head  # bounded rounds, but no unreviewed
                                         #   merge, bot-authored PRs included
    if demo_ok(pr) is false and nothing was pushed this tick
            and no demo is running:
        demo(pr)                           # first demo, a re-demo after a
                                           #   non-exempt push, or after a fix
                                           #   for a failed demo
    if not pending(pr) and not bugbot_open and no finding is unanswered:
        cadence = 15m                      # only the demo, CI, or a late
                                           #   thread can change state now
    else: cadence = 3m
    if the poll's interval is not cadence:
        cancel the poll job; schedule /loop <cadence> in its place
    re-read state                          # the gate must see this tick's
                                           #   push and comment
    if merge_gate(state): merge (squash, --delete-branch); close_out()

function merge_gate(state) -> bool:
    return docs, lint, and test green on the latest commit
       and not pending(pr)                 # no requested review outstanding
       and not bugbot_open                 # any queued or in-progress run on
                                           #   any commit, unless ruled
                                           #   "proceed": its findings land
                                           #   before the merge, until it
                                           #   ends
       and every round with a failed Bugbot run has its rerun(N)
                                           # any round, not only round(pr)
       and reviewed_head is the PR head    # bot-authored PRs included
       and every finding is answered
       and zero unresolved review threads
       and round(pr) ≤ 2
       and no escalation is open
       and demo_ok(pr)                     # demoed, and nothing non-exempt
                                           #   landed after the demo
    # A push restarts CI, not review. When this returns true: merge
    # EXPLICITLY. No --auto. No --admin. Do not ask, do not wait.

function close_out():
    cancel the poll loop
    bd close the bead(s)                  # at merge, not at PR open: a failed
                                          #   demo leaves the bead open
    delete the branch; checkout main; pull
    send the merge recap via beadle-email # to jim@punt-labs.com, every merge,
                                          # unprompted — a permanent record in
                                          # the 8-part recap structure, plus
                                          # the review data block from
                                          # ../.bin/review-metrics.py pr
                                          # punt-labs/vox <N> --format recap,
                                          # pasted verbatim
    start the next unit immediately       # no stopping to report

function local_review(diff):
    # Local rounds are cheap and bugs die here; the PR gate is kept short.
    for round in 1..5:
        if this is the unit's first full diff or diff is 20 lines or more:
            reviewers = code_reviewer + silent_failure_hunter + `qodo review`
        else:
            reviewers = `qodo review`    # a small fix diff
        if diff touches credentials, secrets, or caller text that reaches
                a file, a log line, or a header:
            reviewers += djb             # whatever the diff's size
        if this is not the unit's first full diff:
            scope: regressions this diff introduces, and High findings
                inside it — nothing outside the diff, no Medium or Low on
                unchanged behaviour
        run reviewers in parallel        # qodo review from the worktree,
                                         #   against the pushed base
        re-run once any reviewer that did not complete (error, outage)
        if a reviewer still has not completed:
            report it to the operator
            return blocked               # a review that never ran is not
                                         #   clean
        findings = every reviewer's findings
        if round == 5 and a finding needs a fix:
            report the open findings to the operator
            return blocked               # the caller stops; see below
        answer each finding: fix it, or dismiss it with the exact finding,
            the specific reason, and file:line
        close each local Qodo finding through the Qodo CLI
            (mark-implemented or dismiss, with its local_review_id) —
            an unclosed one comes back as still_open in every later run
        if no finding was fixed:
            return clean                 # nothing found, or all dismissed
        make check; commit
        diff = this round's fix diff     # every change is reviewed,
                                         #   Low fixes included
    # at most four fix rounds, plus a fifth review that only verifies the
    # fourth round's fixes. No deferral beads. Every caller acts only on
    # clean: on blocked, nothing opens and nothing is pushed until the
    # operator rules and local_review then returns clean.
```

Entry and exit for one PR iteration. `merge_gate` in the pseudocode is the
pre-merge subset of these conditions; `ExitPR` describes the state after
close-out completes.

```text
EnterPR ≙ [ LoopState; unit : ℙ BEAD |
  unit ⊆ claimed
  ∧ rollbackCoherent(unit)                -- reverts together sensibly
  ∧ inThroughputBand(unit)                -- above transaction cost,
                                          --   below reviewer degradation
  ∧ (architectural(unit) ⇒ designRatified(unit)) ]  -- operator has ruled

ExitPR ≙ [ Δ LoopState; pr : PR |
  merged(pr)
  ∧ localReviewClean(pr)                  -- a local round that left nothing
                                          --   to fix, BEFORE the PR opened and
                                          --   before every fix push
  ∧ demoOk(pr)                            -- demo_ok: a non-exempt PR was
                                          --   confirmed by ear on demoed_head,
                                          --   and nothing non-exempt landed
                                          --   after
  ∧ ciGreen(head(pr))                     -- docs + lint + test
  ∧ ¬ pending(pr)                         -- every requested review is in
  ∧ ¬ bugbotOpen(pr)                      -- no Bugbot run on any commit of
                                          --   the PR was open at merge
  ∧ bugbotRerunsPosted(pr)                -- each round with a failed run
                                          --   had its rerun(N)
  ∧ reviewedHead(pr) = head(pr)           -- merged code was reviewed, bot-
                                          --   authored PRs included
  ∧ (∀ f : findings(pr) • answered(f))    -- fixed on head, or reasoned
  ∧ round(pr) ≤ 2                         -- round 2 only after a High fix
  ∧ unresolvedThreads(pr) = ∅
  ∧ openEscalations(pr) = ∅               -- each closed by a recorded ruling
  ∧ beadsOf(pr) ⊆ closed′
  ∧ mergeRecapSent(pr) ]
```

### Local review before the PR

Full-diff local review is where issues die cheaply. A local round costs
seconds; a remote review cycle costs minutes to hours. Both `code-reviewer`
and `silent-failure-hunter` run on the full diff — silent-failure-hunter
matters especially in vox, where a swallowed provider error becomes silence
the user cannot diagnose. `qodo review` runs alongside them: it is the
engine that reviews the PR, so what it finds locally does not come back as
a PR round. Local rounds repeat until a round leaves nothing to fix: at
most four fix rounds plus one review that verifies the last fixes. Every
fix diff goes through the same loop before it is pushed,
including fixes made for PR findings. Every finding is fixed now or
dismissed with the exact finding, the specific reason, and the code
reference. Nothing is deferred to a bead. In this repo this loop replaces
the "re-run until zero findings" sequence in the deposited
`.punt-labs/ethos/CLAUDE.md` guide.

The split is deliberate: local rounds are cheap and the PR gate is short.
In the 2026 coverage sample, 28 of the 49 real bugs a reduced PR review
would miss were in code added by fix pushes after the PR opened — the code
that local review of each fix diff now covers.

From the second round on, the review is scoped to what the diff changed:
regressions it introduces and Highs inside it. Without that boundary a
failure-focused reviewer walks outward into code the diff never touched,
and the rounds do not converge. The three reviewers are not substitutes —
in the pilot each one found defects the other two missed — so every full
diff and every fix diff of 20 lines or more gets all three. A fix diff
under 20 lines gets `qodo review` alone, and `djb` joins any diff in its
domain whatever its size.

### Remote review: Qodo and Bugbot, at most two rounds

Qodo is the primary remote reviewer (`.pr_agent.toml`). It reviews when
the PR opens and never on push. High findings post inline as threads, and
the `required_review_thread_resolution` rule in ruleset 13252005 makes each
one block the merge. Medium findings appear in the summary; every Qodo finding is closed
through the Qodo CLI (`mark-implemented` naming the fix sha, or `dismiss`), never a ledger line,
except one reopened by a force-push or rebase (`answered(f)`). Lows are dropped by `comments_routing`.

Bugbot reviews each PR once, on its own, when the PR opens (Cursor
dashboard setting, 2026-10-23); round 1 needs no `bugbot run` comment, and
a push draws no Bugbot review. Its findings are handled whenever they
arrive. It posts fewer comments than Copilot (about 2.4 per PR against
5.4) and had the highest real-bug rate in the 2026 sample (28% of its
comments, against 20% for Qodo and 12% for Copilot). The Cursor security
automation ("Find vulnerabilities") also runs here; its threads are
findings like any other.

Copilot is on a trial (operator ruling, 2026-10-23): one review, requested
at open, on 5 vox PRs after #526, and none on push. The coverage study made
Copilot the only source of 37 of 101 real bugs; the trial counts how many
real bugs Copilot alone finds on top of this local loop, and the operator
keeps it only if that count is not zero. Its findings are findings like
any other.

Round 1 is the review at open. Round 2 — one `/agentic_review` plus one
`bugbot run` — happens only when round 1's fixes included a High, and it is
the last. A new High in round 2 goes to the operator.

This holds only when four settings are in place (pilot `vox-2q95`):

- `.pr_agent.toml` is on `main`. Qodo reads it from the default branch, so
  a PR opened before it merged runs on the portal defaults.
- The `copilot_code_review` rule is gone from ruleset 13252005 (removed
  2026-10-22), so Copilot reviews only when requested.
- Bugbot reviews each PR once, at open, for this repo (Cursor dashboard,
  set 2026-10-23), so a push does not re-run it.
- "Review every push" is set to Disable in the Qodo portal; the portal
  default is Enable.

If a push still starts a Bugbot run, its findings are findings like any
other. Whether a `bugbot run` comment still triggers a run under the
once-per-PR setting is unverified; round 2 posts it, and `bugbot_in(2)`
falls back after 30 minutes if no run starts.

The first two PRs after the pilot (#523, #524) checked the claims:

- No Low finding appeared, and a push drew no new Qodo review. Qodo still
  marks a finding implemented when a push fixes it.
- `/agentic_review` reviewed the new commits; its two new findings were
  both in round-1 fix code.
- Bugbot ran on a push with no `bugbot run` comment, so the mention-only
  setting tried first was not in effect; the operator then set the
  dashboard to review each PR once.
- #524 drew no remote findings in its one round. #523 used both rounds,
  and round 2 raised no new High.

### The audio demo gate

`make check` passing means the code compiles and the tests pass. It does not
mean the feature works — and for vox it cannot, because vox produces audio
that only a human can judge. Before the PR merges, the feature is driven
through its real entry point — a `mic` tool call, a CLI command, a hook fire —
with the expected outcome written down first, after `make install` and
`vox daemon restart` so the running daemon is the just-built code. Machine
evidence comes from `mic:status`, `vox doctor`, and a grep of `vox.log`. Then
the operator confirms what no introspection API can attest: that the audio
sounded right — clean, the correct voice, the music ducked, the chime fired.
Run the canonical flight in [`docs/testing/manual-tests.md`](testing/manual-tests.md)
and ask after each audible step, immediately, because audio impressions decay
within seconds. This is the one human gate in the loops, and it is a demo, not
a diff.

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
        worker commits locally           # each commit passes make check
        result   = worker submits
        findings = evaluator review      # a DISTINCT specialist
                 + leader verification   # make install; vox daemon restart;
                                         # make test on the installed artifact;
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
  ∧ (∀ c : commitsOf(m) • checkGreen(c))  -- every commit passed make check
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
`CLAUDE.md`, `DESIGN.md`, `README.md`, this `WORKFLOW.md`, design docs, and
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
Qodo or Bugbot reports on the PR, the leader reads each finding and hands
the mechanical fix to a bare `Agent()`, then pushes and resolves the thread
itself.

### The lifecycle

1. **Contract.** The leader writes the mission contract: the problem, the
   invariants, the quality bar, and the commit discipline — one commit per
   logical step, each passing `make check`, never more than thirty minutes of
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
   `make check` — zero exceptions, zero unauthorized suppressions.
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
   `make install`, `vox daemon restart`, `make test` against the installed
   artifact, and exercise the change through its real entry point with the
   expected output written first — one invalid input, one missing/erroring
   dependency, one boundary. Run the applicable local review agents on the
   mission diff and fix every finding; a dismissal requires the exact finding,
   the specific reason it does not apply, and the code reference. If the result
   raises a design question, it goes to the operator as a concrete decision —
   with a recommendation — before any dependent mission dispatches. Then close
   the mission.

## Invariants

1. **Beads are the single funnel.** Every piece of work is a bead before it
   is anything else; external queues (issues, alerts) drain into it at intake.
2. **A satisfied merge gate means merge now.** The gate is the only path to a
   merge, and nothing waits once it passes. Merge is always explicit — never
   `--auto`, never `--admin`.
3. **Do or do not.** A finding is fixed in this PR or dismissed with a
   reason, the moment it arrives. It is never deferred to a bead or a
   follow-up PR. A thread is resolved only once it is answered per
   `answered(f)` — a Qodo finding closed through the Qodo CLI
   (`mark-implemented` or `dismiss`), any other finding by a landed commit
   or a ledger reason — one by one, never in bulk.
4. **A push restarts CI, not review.** Local rounds run until clean (at most
   four fix rounds plus a verifying review) before the PR opens and before
   every fix push. PR rounds are at
   most two, and the second only after a High fix.
5. **Defects flow inward, scope flows outward.** A defect found while a PR is
   open is fixed in that PR. Only genuinely new lines of work become beads;
   a review finding is never new work.
6. **The operator gates the audio demo and direction, never diffs or
   sequencing.**
7. **Close-out is inside the loop.** The recap email, branch hygiene, and
   starting the next unit are steps of the loop, not afterthoughts.
8. **No migration, compat, shim, or version-bridge code.** Punt Labs products
   have no installed base to migrate; the leader strikes any such element at
   design review, before it reaches implementation.
9. **The daemon is the audio host.** `voxd` owns playback, the cache, the
   music program, and the recordings on disk; clients are thin controllers.
   Work that touches audio, audio files, or daemon-owned state routes through
   the daemon so it behaves the same whether the daemon is local or remote.
10. **Never touch `vox.md` / `vox.local.md` directly.** The daemon writes
    them; all state is reachable through the `mic` tools. Racing the files
    corrupts state.
