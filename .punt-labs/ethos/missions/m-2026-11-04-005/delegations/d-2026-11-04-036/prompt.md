You are the worker on ethos mission m-2026-11-04-005 (design). Read the contract first: `ethos mission show m-2026-11-04-005`. It carries the goal, constraints and success criteria; follow it.

Work in the worktree <repo>/.claude/worktrees/daemon-stop-start (branch feat/vox-9354-daemon-stop-start). Your only write is docs/daemon-lifecycle-design.md. Read the code to ground the design; do not change any code. You may run read-only commands (`vox daemon status`, `vox daemon --help`, `systemctl status`/`show` for the voxd unit) to confirm how the backend behaves; do NOT stop, start or restart the real daemon.

Before committing, set your biff plan (e.g. "vox-9354: daemon stop/start design") if you have the biff plan tool; if you do not, proceed — the session plan is set. Commit the design doc alone (`git add docs/daemon-lifecycle-design.md`), message `docs(design): vox daemon stop/start lifecycle (vox-9354)` plus a blank line and `Co-Authored-By: Claude Opus 5.5 <[redacted-email]>`. Run `npx markdownlint-cli2 docs/daemon-lifecycle-design.md` with zero findings before committing.

Then submit your result: `ethos mission result m-2026-11-04-005` (see its --help for the fields), naming the file you changed and the commit sha.

Commit on your branch; do not push, do not touch PRs or merge — the leader does that. If a design question needs an operator decision (for example how stop should behave when the service is not installed, or whether stop should disable the unit), state it in the doc as an open question with your recommendation rather than guessing silently, and report it.