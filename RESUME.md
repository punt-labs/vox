# Resume — vox session continuation (updated 2026-07-11/12)

You are **Claude Agento (`claude`)**, COO/VP-Eng for Punt Labs, in the **vox** repo (`/Users/jfreeman/Coding/punt-labs/vox`). The COO **delegates all code to ethos agents** (never writes product code) and authors only: CHANGELOG, CLAUDE.md, DESIGN.md, README.md, design docs, memory/plan files (like this). Read `CLAUDE.md` + `../punt-labs/CLAUDE.md` for the full workflow.

## Overarching goal

Get vox **publish-ready** ("zero-bug-bounce" — Jim wants to be proud to publish). Two live threads: (1) the **surface audit + repositioning** (`vox-iyqq`), and (2) the residual **publish worklist**.

## IMMEDIATE NEXT ACTION

Start **`vox-yn8u`** (MCP `unmute`→`say` tool rename) unless Jim redirects: `cd vox && bd update vox-yn8u --status in_progress`, branch, delegate to rmh (MCP server domain) or mdm, review, PR, `--auto` merge.
**Why:** live inconsistency — the CLI now says `vox say` (shipped #316) but the MCP tool is still `unmute` (`server.py:358`). Rename the MCP tool → `say` + fix all callers: `hooks/suppress-output.sh:71`, `hooks/session-start.sh:84`, the `/unmute` skill (`commands/unmute.md`), docs. **Keep `/unmute` + `/mute` slash commands** (they're the voice-mode-on / chimes-only toggle pair) — repoint them to the `say` tool. Also in vox-yn8u: `who()` has a no-op `language` param (implement or drop); consider a stop-speech tool. **Test carefully** — this drives THIS session's voice; verify via `uv run vox`/tool before merge.

## The active initiative: `vox-iyqq` (surfaces + repositioning)

Jim: prfaq.tex + README undersell vox — it's **more than notifications** (engaging/fun agent-human collaboration: voices, `/vibe`, music, personality), and it has **three surfaces** (MCP, CLI, Python API) but only MCP reads as first-class. Per Jim's directive: **audit each surface, file one audit bead per surface with issues**, fix them, THEN reposition docs against solid surfaces.

Per-surface audit beads:

- **`vox-2vto` (CLI) — CLOSED (#316).** Shipped `vox say` (from `unmute`), `vox voices`, post-subcommand `--json`, stdin. Polish remainder → **`vox-cnak` (P3)**.
- **`vox-yn8u` (MCP) — OPEN ← NEXT** (see above).
- **`vox-1hfd` (Python API) — OPEN** (finding #1 done in #315: `VoxClient`/`VoxClientSync`/`SynthesisSpec`/`SynthesizeResult` now top-level exported). Remaining: export exception types (`VoxdConnectionError`/`VoxdProtocolError`) + add a common `VoxError` base; `program_*`/`health()` return raw `dict` → typed result dataclasses; `PromptSet` unexported; docstrings leak internals.

Then **`vox-iyqq` itself**: full prfaq.tex (via prfaq-dev `/prfaq:feedback`) + README repositioning — lead with vox as the engaging voice+audio collaboration layer (notifications = one use), MCP/Python-API/CLI parallel first-class. (Minimal README "Python API" section already landed in #315.)

## Publish worklist (residual, ranked by user-visibility)

- **`vox-ig52` (P1)** — music failures invisible to clients ("generating…" then dead air). Partially shipped; full observability contract (`music_state` incl. retrying/failed, `music_last_error`, error classification) spec'd in `docs/vox-ig52-music-resilience.md`, NOT fully implemented. **Highest-value user-facing.**
- **`vox-ekmx` (P3)** — audio clipping artifact at end of output (audible bug).
- **`vox-qefg` (P2)** — remove `@` sigil from slash commands + CLI help (interferes with Claude Code).
- **`vox-n3t1` (P2)** — purge dev-process refs from docstrings.
- **`vox-cnak` (P3)** — CLI polish (command grouping, install symmetry, `--json` on music/daemon, `--rate` `%%` help leak, voice-setting consistency, thin toggle help, empty-`--from`-segments silent-success guard).

## Shipped this session (all merged to main)

- **#312 (vox-26i1)** coupling+suppression ratchet merge-base parity + CI enforcement.
- **#313** Z spec of the 3 ratchet tools (`tools/docs/ratchet.tex`, fuzz-clean).
- **#314** 3 ratchet gaps the Z model surfaced (oo baseline hardening, oo empty-baseline intercept, suppression update never-loosen).
- **#315 (vox-iyqq #1)** public Python API export + `check-coupling --relax` audited waiver + docs.
- **#316 (vox-2vto)** CLI first-class.
- Bead triage: closed stale/done beads (vox-y3om/dyw4/p6ce/jh4z/0g4h/ou7o/ek73/wy2g/5ps/0xa9).

## Cross-repo threads (owned elsewhere, NOT vox publish blockers)

- **ethos `ni0y`/DES-057 — MERGED** (repo-authoritative resolution + `ethos vendor` + ext `.local` split + DES-044 ext-read carve-out), with **vox's (a)/(b)/(c) as acceptance criteria**. v1 impl pending Jim's dispatch. **vox is first adopter** — agent `claude:tty3` pings at impl + ship; then vox re-vendors `.punt-labs/ethos/` WITH ext (restores agent memory wiring).
- **quarry `fpc5` — MERGED** (capture PII redaction live; validated vs vox's 598-finding corpus → 0). **`ow3k`** (private `<repo>-quarry` shadow sync) next — agent `claude:tty8` hands over the sync pattern (shadow-remote config + commit/push + doctor check); then **wire vox→vox-quarry**.

## The ratchet tooling

`tools/oo_ratchet/` (check-oo), `tools/coupling/` (check-coupling), `tools/suppression/` (check-suppressions): now (a) formally modeled in `tools/docs/ratchet.tex` (fuzz `-t` exit 0), (b) fully fail-closed, (c) **all three share the identical audited `--relax` waiver** (coupling gained it #315). **Candidate for `punt-ratchet` standalone extraction AFTER vox clean** (memory: `project-extract-ratchet-standalone`).

## CRITICAL operational rules / gotchas

- **gh auth:** env `GH_TOKEN`/`GITHUB_PERSONAL_ACCESS_TOKEN` 401 every call — **prefix EVERY `gh` with `env -u GH_TOKEN -u GITHUB_PERSONAL_ACCESS_TOKEN`**.
- **Merge mechanics (ruleset, not classic protection):** requires `docs`+`lint`+`test` green, `copilot_code_review` (a FRESH Copilot review of HEAD — **dismissed on every push**, takes a few min), and **thread resolution** (0 unresolved). Approvals = 0. Use **`gh pr merge N --auto --squash --delete-branch`** (arms auto-merge; fires when clean). **NEVER `--admin`** (forbidden bypass). Resolve threads via `.tmp/resolve_thread.graphql` (`gh api graphql -F query=@.tmp/resolve_thread.graphql -f t=<threadId>`); fetch unresolved IDs via `.tmp/threads_query.graphql`. Both files exist. `.tmp/threads_detail.graphql` gets thread bodies.
- **PR monitoring:** `/loop 3m` polling `gh pr view N --json state,mergeStateStatus` + `gh pr checks N`. On MERGED → sync main, `git branch -d`, `bd close`, recap email. Copilot/Bugbot reliably find real edge cases (fail-closed I/O, naming, silent defaults) — fix every valid one, re-push, resolve threads. Ratchet PRs attract many I/O-robustness findings.
- **beads:** `cd <repo> && bd ...` (cwd matters for prefix/label). `bd create` does NOT auto-apply `repo:vox` → `bd label add <issue> repo:vox` after (**issue arg FIRST**). Prefix=`vox`. **NO backticks in `bd -d`** — shell runs them as command-substitution (mangled a bead this session).
- **Agent liveness:** judge by **FILE mtime, NOT output-transcript mtime** — heavy agents (jms/mdm/rmh) do long reasoning turns; transcript looks 30-90 min stale while file-writes are seconds fresh. Do NOT `TaskStop` a producing agent (file-writes recent). BUT agents DO stall (mdm + rmh both stalled this session). If an agent did complete-but-uncommitted work then went silent (no file-write 10+ min): verify done (`make check` + functionally exercise), then commit **by proxy**. Put commit-per-step in every brief.
- **Coupling `--relax` exists (since #315):** justified under-ceiling coupling increase → `oo_coupling.py --relax FILE --justify "..."` (audited, scoped-to-change, per-metric), **NOT** hand-editing `.oo-coupling-baseline.json`. Same for check-oo. **Re-lock a relax at the CURRENT file state** — editing the file after relaxing makes the lock stale → check fails (bit us this session; mdm left a stale relax).
- **OO ethos:** "good deed, not squeeze under the limit" — pay down via real extraction; NEVER shave comments/lines to scrape past (rmh started to this session, self-corrected). `--relax` only for genuinely-unavoidable feature substance (e.g. Typer flat params), justified.
- **make check green before EVERY commit**; don't push before `make check` completes. After src changes: `make install` + `vox daemon restart` before testing MCP/hook/synthesis paths (daemon serves old code until restarted). CLI tests: `uv run vox ...` runs source directly.
- **Recap email to <jim@punt-labs.com> at every close** (beadle `send_email`) — permanent record, never skip.
- **NEVER touch `.punt-labs/ethos/`** (vendored; its dirty `git status` files are session-hook artifacts — exclude when staging). Its `m-2026-07-04-019` mission-log churn is bookkeeping.
- **Session audio state:** vibe=auto, music=off, vox=muted (chimes only / `speak n`). Stop-hook `♪` prompts want a 1-2 sentence spoken summary via the `unmute` MCP tool with `ephemeral=true` (chimes only while muted).

## Delegation pairings (ethos agents, run BACKGROUND)

rmh=Python core/client/daemon/MCP; gvr=provider impls + evaluator; mdm=CLI + hooks; jms=Z-spec (authored fuzz); adb=infra/CI; djb=security; kpz=audio/ML; claudia=prose (global-only). Worker + evaluator must differ.

## Notable session outcomes (context for judgment)

- Two honest process notes disclosed to Jim: **mdm stalled** mid vox-2vto (finished by proxy after verifying its work was complete); **rmh briefly gamed the ratchet** (shaving comments) then self-corrected to a real good-deed refactor. Report such things plainly; don't sweep.
- The recurring pattern: honest work (z-spec, surface audit) keeps surfacing real tooling gaps (4 so far), and Jim closes each properly rather than papering over — hence the ratchet is now fully hardened + waiver-parity.
