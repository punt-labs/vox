You are the worker on ethos mission m-2026-11-03-008 (bead vox-u7gz). Read the contract first: `ethos mission show m-2026-11-03-008`. It holds the goal, evidence, write-set, and success criteria; follow it exactly.

Worktree: <repo>/.claude/worktrees/pause-button (branch fix/vox-u7gz-pause-button). Work only there.

Order: a failing test that reproduces the operator's sequence comes first and is committed on its own; then the root-cause fix. If the root cause turns out to be in lux (how lux applies a patch to a button's publish topic), stop and report with the evidence instead of working around it in vox.

Run `biff plan "vox-u7gz: fix music pause button"` before your first commit. `make check` green on every commit, full output read. Commit only files you changed. Commit on your branch; do not push, do not touch PRs or merge — the leader does that. When done, submit with `ethos mission result` and report: the root cause (file:line), the failing-test commit sha, the fix sha, and the test names covering the three button states.