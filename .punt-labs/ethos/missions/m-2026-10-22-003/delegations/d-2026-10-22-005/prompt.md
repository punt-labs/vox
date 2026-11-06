You are the worker on ethos mission m-2026-10-22-003 (bead vox-rmyx, P1 security, round 2 of your earlier lock bump).

Read the full contract first: `ethos mission show m-2026-10-22-003`. It carries the goal, the write set (pyproject.toml, uv.lock, CHANGELOG.md), the success criteria, and the working directory. Also read gvr's round-1 reflection, listed in the contract's inputs. Everything you need is there; this prompt only tells you how to run it.

How to work:
- Work only in the worktree <repo>/.claude/worktrees/vox-rmyx-security on branch fix/vox-rmyx-security-lock-bump, on top of your commit e67864e. Use absolute paths. Prefix commands that need the repo environment with `direnv exec <repo>`.
- Run `biff plan "vox-rmyx mcp floor"` before your first commit, or the write gate blocks it.
- Run `make check` and read its full output — never pipe it through tail, head or grep.
- No suppressions, no `--no-verify`, no rebaseline. Do not amend or force-push e67864e; add new commits.
- If the re-lock moves any package version, stop and report it.
- Commit and push your feature branch. Do not open, update or comment on a PR, do not request reviews, do not merge, do not push to main — the leader does all of that.
- Finish by submitting your result with `ethos mission result m-2026-10-22-003`, including the diff stat, the mcp requirement line in uv.lock before and after, and the make check summary lines.