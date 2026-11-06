You are the worker on ethos mission m-2026-11-04-011 (bead vox-9354: `vox daemon stop` and `vox daemon start`, with restart composed from them). Read the contract first: `ethos mission show m-2026-11-04-011`. The contract is the what; this prompt is the how.

Work only in the worktree <repo>/.claude/worktrees/daemon-stop-start (branch feat/vox-9354-daemon-stop-start). Use absolute paths and `make -C <worktree>` / `git -C <worktree>`; one command per Bash call (no &&, ;, |). Before your first commit run the biff plan command for your session (`biff plan "vox-9354 daemon stop/start"`) or the write gate blocks you.

Read before coding: docs/daemon-lifecycle-design.md (final design, commit 79fb195) and docs/daemon-lifecycle.tex (Z model), plus vox CLAUDE.md and ../.claude/rules/python-*.md. Implement the design as written. If code reality contradicts the design, stop and report the conflict — do not improvise.

Discipline: tests lead (the §10.1 tests named per outcome row and asserting P1–P5 by name). Commit incrementally — one commit per logical step, `type(scope): description`, `git add <file>` only (never -A/-a). Each commit must pass `make -C <worktree> check` with no new suppressions. Never more than 30 minutes uncommitted. After code passes, `make update-oo` and stage the ratchet files with that commit; any rebaseline must be scoped to named file+metric entries with a justification.

Do not stop, start, or restart the real voxd daemon, and do not run `make install` — the leader runs the demo. Do not touch README.md or CHANGELOG.md. Never read or edit .punt-labs/vox/vox.md or vox.local.md.

Commit on your branch; do not push, do not touch PRs or merge — the leader does that. When done, submit your result with `ethos mission result m-2026-11-04-011 ...` and report: commit shas, make check summary (test count), the §10.1 test names, OO ratchet outcome, and any design conflict you hit.