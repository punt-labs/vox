# PR loop: pseudocode, terms and entry/exit schema

The executable specification of Level 2 of [`WORKFLOW.md`](WORKFLOW.md): the
`pr_loop`, `demo`, `poll_tick`, `merge_gate`, `close_out` and `local_review`
pseudocode, the terms they read from GitHub, and the `EnterPR`/`ExitPR` Z
schemas. It is linked from `WORKFLOW.md`, not `@`-imported, so a session's
instruction load stays under the harness limit. Read it before starting a unit
of work (step A holds the design-ratification gate for architectural units),
before changing or running any step of the PR loop, and whenever a local
review, merge gate, review round, demo or escalation question comes up.
`LoopState`, which `EnterPR` and `ExitPR` read, and the sizing rule behind
`inThroughputBand` are defined in `WORKFLOW.md`.

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
    make check                            # full gate before PR, not per commit
    if local_review(full diff) is blocked: stop the unit until the
        operator rules                    # local_review: after close_out

    # C. SHIP — the PR opens before any demo, so remote review runs while
    #    the operator is away; the merge waits for demo_ok (merge_gate)
    push; create the PR (description carries the verification playbook)
        # Qodo reviews once, on open (.pr_agent.toml)
    if copilot_trial(pr): request a Copilot review   # once, at open; none
                                                      #   on push
    schedule a background poll with /loop 3m  # never gh pr checks --watch,
                                              # never foreground sleep loops

    # D. EXERCISE BEFORE MERGE — a green automated tier that drives the
    #    changed behavior through its real entry point is the proof
    #    (coverage, rows_green); a demo runs only for a tier gap. The demo is
    #    confirmed by ear when audible(pr), the one human gate;
    #    otherwise by the leader's recorded run. poll_tick is
    #    its only caller, so it runs at most once at a time, and it runs
    #    again after a non-exempt push or a failed demo

function demo(pr):
    # called only when gaps(pr) is not empty: some changed behavior has no
    # automated tier exercising it. The demo covers every row of gaps(pr)
    # and nothing else; the covered rows are proven by their tiers
    no push to the branch, and no other demo, while this one runs
    if audible(pr) and the operator is not available: return
                                                # no comment; a later tick
                                                #   tries again; a PR that
                                                #   plays nothing needs no one
    from a clean worktree at the PR head H:
        make install; vox daemon restart  # the daemon serves old code until
                                          #   restarted; a stale daemon demos
                                          #   the old build
    write down the expected outcome, then drive the real entry point
        (mic tool, CLI command, hook fire)
    capture machine evidence: mic:status, vox doctor, and grep of vox.log
    if not audible(pr):                 # a reading or a refusal: nothing
                                        #   played, no ear to ask
        if the output matches: comment "demo confirmed (not audible): H"
            with the exact command and output pasted    # the leader's run
                                                        #   IS the demo
        else: comment "demo failed: H" with the output; a finding as below
        return
    run the roundtrip tier (make test-roundtrip; make test-roundtrip-live
        where the credentials are present; make test-roundtrip-say on
        macOS when the say provider changed): a fixed corpus is synthesized
        through the providers, transcribed back and its word error rate
        checked, which is the machine verification that the synthesis
        path still produces intelligible speech; a failed roundtrip is a
        finding and the ear is not asked until it passes
    run the canonical flight in docs/testing/manual-tests.md and ask the
        operator after EACH audible step for what the corpus cannot judge:
        that the step said the right words, in the right voice, with the
        delivery, the ducking, the chime and its timing; the ear is asked
        only for those
    if the operator confirms: comment "demo confirmed by ear: H" with the
        operator's words for each audible gap, the command and output
        for each silent gap, and the roundtrip result
                                    # demoed_head; only a PR that changes
                                    #   a non-exempt file reaches here
                                    #   (demo_ok is false)
    else: comment "demo failed: H" with what the operator heard; the
        failure is a finding like any other (fixed, local_review on the
        fix diff, pushed), and the next tick demos the new head
    # A finding that surfaces after the merge (a hook verdict, a late
    # review) is recorded on the merged PR with the reasoning, by its
    # source's path in answered(f) (a Qodo id through the Qodo CLI, any
    # other by a ledger comment), and the next unit starts; it is never a
    # blocking question to the operator. The
    # operator's instruction to run a command is the ruling, not a request
    # for another question.

# Terms, all read from GitHub (Qodo's login is qodo-code-review[bot]).
# The rules are model-checked in punt-kit standards/pr-review-model.tex,
# which still models Bugbot; this loop drops its Bugbot conjuncts.
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
#   devin_in     = a review or review comment by devin-ai-integration
#                  exists on pr, or 30 minutes have passed since the PR
#                  opened (a clean Devin run may post nothing; the timeout
#                  covers that and an outage)
#   pending(pr)  = (not skipped(pr) and not qodo_in(round(pr)))
#                  or not copilot_in or not devin_in
#                  # a Copilot review someone requests on a skipped
#                  # (bot-authored) PR still counts
#   finding      = any unresolved review thread, from any author, any
#                  Medium in Qodo's summary, or a finding reopened after a
#                  force-push or rebase (answered(f)) — whenever it arrives,
#                  late ones included
#   answered(f)  = one path per source, never both:
#                  - a finding with a Qodo id (from the CLI read): its
#                    attribution_status is closed (implemented, dismissed,
#                    outdated); close it with `qodo pr-review-session
#                    mark-implemented` (the explanation names the fix sha)
#                    or `dismiss` plus the reason and file:line. A ledger
#                    line does not close it, except a reopened one (below).
#                  - any other finding — Devin Review, Copilot,
#                    a human, the local outage fallback: fixed by a commit
#                    on the PR head, or a ledger comment gives the reason
#                    and file:line.
#                  Whatever the source, and whether or not it was
#                  escalated, a finding answered by a fix is answered only
#                  once its fix commit is on the PR head and passed
#                  local_review. A finding whose latest ruling is "fix" is
#                  answered only by its fix; a "dismiss" ruling, including
#                  one after a blocked fix, answers it once recorded.
#                  After a force-push or rebase, a finding answered by a
#                  fix, from any source, is reopened, unanswered, only if
#                  that fix's change is no longer in the branch; new commit
#                  ids alone do not reopen it. The leader unresolves a
#                  reopened finding's review thread; a finding with no
#                  review thread (a local or a Qodo summary finding) is
#                  recorded by a ledger line
#                  `reopened: <finding> <fix sha>`. A Qodo finding is
#                  answered again by a ledger line naming its Qodo id and
#                  a new fix sha, which is its recorded fix; any other by
#                  a new fix commit; both under the fix rule above.
#                  A finding repeated from an earlier round keeps its
#                  answer, unless it was reopened.
#   new High     = a High raised by a round-2 review (one that started at
#                  or after trigger(2)) and not raised in an earlier round;
#                  a manual review may re-scan the whole PR and repeat old
#                  findings, and a round-1 High that arrives late is not
#                  new — it takes the normal fix path
#   escalation   = the leader's "escalated:" comment on one new round-2 High,
#                  on a fix-ruled finding whose fix was blocked in
#                  local_review, or on a blocked local_review; closed by a
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
#   demo_exempt(f) = f is under docs/, tests/, or .github/, is a ratchet
#                  file (.oo-*, .suppression-*), is one of the
#                  files .pr_agent.toml, .gitignore, .cursor/BUGBOT.md,
#                  or is a documentation file wherever it sits (*.md,
#                  *.tex, *.bib, or a *.pdf built from one: README.md,
#                  DESIGN.md, CHANGELOG.md, the PR/FAQs prfaq*.tex with
#                  their .bib and .pdf, the guide copies this repo keeps
#                  under .punt-labs/),
#                  and is not under plugin/ or src/ — commands, skills,
#                  agents, and hooks under plugin/ are behavior even when
#                  Markdown, because Claude Code executes them, and a
#                  Markdown file under src/ ships in the package (the
#                  guide template `vox enable` writes into other repos);
#                  src/, a dependency, or an install change can change
#                  what the operator hears. A settings file is not
#                  documentation whatever its extension: a tool's
#                  <tool>.md or <tool>.local.md directly under .punt-labs/
#                  or .punt-labs/<tool>/ is configuration, so it is never
#                  exempt (.punt-labs/vox/vox.md holds vox's provider,
#                  voice, notify and speak). Outside plugin/ and
#                  src/, every documentation update is demo-exempt
#                  (operator ruling, 2026-11-27): such a file is written
#                  for people and agents. Three vox commands read one:
#                  the guide row of `vox doctor` compares
#                  .punt-labs/vox/CLAUDE.md with the packaged template
#                  and reports drift, and `vox enable` and `vox disable`
#                  read the root CLAUDE.md to add or remove the guide's
#                  import line; none plays anything, and both files
#                  stay exempt
#   coverage(pr) = the leader's latest "tier coverage: H" comment, valid
#                  when H is the head, or an ancestor of it and every file
#                  changed since H is a demo_exempt documentation file
#                  (*.md, *.tex, *.bib or a *.pdf built from one). tests/
#                  and .github/ are demo_exempt but are not documentation:
#                  a later change
#                  to a test, a workflow or any non-exempt file ends its
#                  validity, so removing or weakening a named test, or
#                  dropping its tier from CI, forces a new comment. With no
#                  valid comment, a PR that changes a non-exempt file has
#                  no coverage. The comment has one row for every behavior
#                  the PR changes in a non-exempt file. A covered row
#                  names an automated tier and a test in it that exercises
#                  the behavior through its real entry point: subprocess,
#                  e2e, call_e2e, roundtrip (offline on every PR; the live
#                  run for a provider change), playback, levels, agent,
#                  live_call, or a default-tier test only when it drives
#                  the real entry point. A test against mocks does not
#                  count: it exercises the code, not the entry point. A
#                  gap row names a behavior no tier exercises and marks it
#                  audible or silent. What no tier judges is always a gap
#                  when the PR changes it: which voice speaks, the
#                  delivery, ducking, chime timing, and what the Lux
#                  display shows
#   rows_green(pr) = coverage(pr) is valid and the tier of every covered
#                  row is green on the PR head: its CI check, or, for a
#                  tier that does not run on a PR (the live roundtrip,
#                  playback, levels, agent, live_call), the leader's run
#                  at H of the valid coverage comment, with the command
#                  and output pasted in that comment (every file changed
#                  since H is documentation, so the run still describes the
#                  head). A covered row on such a tier with no pasted run
#                  makes the comment invalid, so the next tick runs the
#                  tier and posts the comment again. A tier that is red or still running is a CI
#                  state to wait for or fix; it never calls for a demo
#   gaps(pr)     = the gap rows of the valid coverage(pr). The gate exists
#                  to exercise the code before the merge; a green tier
#                  that does so is the proof, and a demo is for a tier gap
#                  (operator ruling, 2026-11-27)
#   audible(pr)  = some row of gaps(pr) is marked audible. A gap is
#                  audible when its behavior can change what is heard; a
#                  gap that only reads or reports state (a status reading,
#                  a doctor row, a refusal) is silent wherever its file
#                  sits. Otherwise the file decides, and these can change
#                  what is
#                  heard (demo_exempt files are not counted): under
#                  src/punt_vox/providers/, src/punt_vox/voxd/ (every file:
#                  port_file.py publishes the port clients need to speak),
#                  the cache, quips, normalize.py,
#                  hooks.py, server.py, client.py, plugin/, a dependency or
#                  lock file, the Makefile, or the service installer and
#                  its unit, environment and key files. Outside voxd/, a
#                  file that only reads or reports state (service/*status*,
#                  *health*, *probe*, lifecycle_status.py, doctor*,
#                  cli_status.py, cli_doctor.py) is not audible;
#                  lifecycle_start.py and lifecycle_stop.py start and stop
#                  the daemon that plays, so they are audible. Any other non-exempt file
#                  is audible: the list fails closed
#   demoed_head  = the commit named in the leader's latest "demo confirmed"
#                  comment, newer than any "demo failed:" comment, when
#                  that comment lists every row of gaps(pr) with its
#                  result. The form is checked: "demo confirmed by ear: H"
#                  counts for any PR when it records a passed roundtrip
#                  result, the operator's words for each audible gap, and
#                  the command and output for each silent gap; "demo
#                  confirmed (not audible): H" with the command and
#                  output pasted counts only when audible(pr) is false, so
#                  the leader's own run can never confirm an audible gap
#   demo_ok(pr)  = every file the PR changes is demo_exempt; or
#                  rows_green(pr) and gaps(pr) is empty; or rows_green(pr)
#                  and demoed_head
#                  exists, is the head or an ancestor of it, and every file
#                  changed since it is demo_exempt. A demo never stands in
#                  for a covered row: the tiers named must be green whether
#                  or not there is a gap
#   finding also = the leader's latest "demo failed:" comment, until a
#                  later demo-confirmed comment whose form counts under
#                  demoed_head

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
    for each finding not answered(f) and not under an open escalation
            nor in a diff whose block escalation is open:
        if N == 2 and f is a new High with no ruling:
            post an escalation comment; the operator rules
        else:
            fix it now (answered after the push, below), or answer it by
                its source's path (answered(f))
            # never re-litigated; Qodo Lows are dropped by comments_routing;
            # no deferral issues; the latest ruling governs
    for each escalation the operator has ruled on:
        record the ruling in a ledger comment   # closes the escalation,
                                                #   not the finding
        a "dismiss" ruling answers the finding (a Qodo id: `dismiss`)
    if fixes were made: delegate them to a bare agent; commit  # commit hook
        make check                        # full gate at fix handoff
        if local_review(the fix diff) is blocked:
            post an escalation comment naming the blocked fix commit and
                what blocked it, and one on each fix-ruled finding in the
                diff; every other finding in the diff stays unanswered
                and is taken again after the operator rules on the block:
                "retry" (the fix is reworked and passes local_review
                before any push) or "drop" (the blocked fix commit is
                discarded unpushed and its findings return unanswered,
                and the next local_review starts fresh, not resumed, so
                the dropped fix's closures carry no weight); either
                ruling's ledger entry closes the escalation; stop
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
        comment "/agentic_review"             # opens round 2, the last
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
    if some changed file is not demo_exempt and coverage(pr) is not valid:
        run any tier a covered row will name that does not run on a PR
        comment "tier coverage: H": one row per changed behavior, with the
            tier and test that exercise it through its real entry point
            (and the pasted run, for a tier outside PR CI), or "gap",
            marked audible or silent, with what is not exercised
        # a gap a test could close is a finding like any other: the test
        # is written, reviewed and pushed in this PR, the comment is posted
        # again with the row covered, and no demo is run for it
    if gaps(pr) is not empty and demo_ok(pr) is false
            and rows_green(pr) and nothing was pushed this tick
            and no demo is running:
        demo(pr)                           # a tier gap only: first demo, a
                                           #   re-demo after a non-exempt
                                           #   push, or after a fix for a
                                           #   failed demo. A red or
                                           #   running tier is never
                                           #   demoed around
    if not pending(pr) and no finding is unanswered:
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
       and reviewed_head is the PR head    # bot-authored PRs included
       and every finding is answered
       and zero unresolved review threads
       and round(pr) ≤ 2
       and no escalation is open
       and demo_ok(pr)                     # every covered row's tier green,
                                           #   every gap demoed, and
                                           #   nothing non-exempt landed
                                           #   after
    # A push restarts CI, not review. When this returns true: merge
    # EXPLICITLY. No --auto. No --admin. Do not ask, do not wait.

function close_out():
    cancel the poll loop
    set the issue(s) Done                 # at merge, not at PR open: a failed
                                          #   demo leaves the issue open
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
            make check                   # full gate at review handoff
            return clean                 # nothing found, or all dismissed
        commit                           # commit hook checks this fix
        diff = this round's fix diff     # every change is reviewed,
                                         #   Low fixes included
    # at most four fix rounds, plus a fifth review that only verifies the
    # fourth round's fixes. No deferral issues. Every caller acts only on
    # clean: on blocked, nothing opens and nothing is pushed until the
    # operator rules and local_review then returns clean.
```

Entry and exit for one PR iteration. `merge_gate` in the pseudocode is the
pre-merge subset of these conditions; `ExitPR` describes the state after
close-out completes.

```text
EnterPR ≙ [ LoopState; unit : ℙ ISSUE |
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
                                          --   exercised by green tiers
                                          --   (rows_green), and any gap was
                                          --   confirmed on demoed_head, by
                                          --   ear when audible(pr), and
                                          --   nothing non-exempt landed after
  ∧ ciGreen(head(pr))                     -- docs + lint + test
  ∧ ¬ pending(pr)                         -- every requested review is in
  ∧ reviewedHead(pr) = head(pr)           -- merged code was reviewed, bot-
                                          --   authored PRs included
  ∧ (∀ f : findings(pr) • answered(f))    -- fixed on head, or reasoned
  ∧ round(pr) ≤ 2                         -- round 2 only after a High fix
  ∧ unresolvedThreads(pr) = ∅
  ∧ openEscalations(pr) = ∅               -- each closed by a recorded ruling
  ∧ issuesOf(pr) ⊆ closed′
  ∧ mergeRecapSent(pr) ]
```
