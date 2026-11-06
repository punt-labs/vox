You are the worker on ethos mission m-2026-10-22-035 (bead vox-tvbi, P1 security).

Read the full contract first: `ethos mission show m-2026-10-22-035`. It carries the goal, the write set (uv.lock and CHANGELOG.md only), the success criteria, and the working directory. Everything you need is there; this prompt only tells you how to run it.

How to work:
- Work only in the worktree <repo>/.claude/worktrees/vox-tvbi on branch fix/vox-tvbi-certifi-otel. Use absolute paths. Prefix commands that need the repo environment with `direnv exec <repo>`.
- Never print a process environment (env, printenv, set, /proc/<pid>/environ). It holds live API keys. If you need one variable, read only that variable.
- Run `biff plan "vox-tvbi lock bump"` before your first commit, or the write gate blocks it.
- Run `make check` and read its full output — never pipe it through tail, head or grep.
- No suppressions, no `--no-verify`, no rebaseline. If a gate fails on a lock-only change, stop and report exactly what failed.
- If an upstream change would need a code change, stop and report it; code is outside your write set.
- Commit and push your feature branch. Do not open, update or comment on a PR, do not request reviews, do not merge, do not push to main — the leader does all of that.
- Finish by submitting your result with `ethos mission result m-2026-10-22-035` (see `ethos mission result --help` for the format), including: the old→new version table, every package that moved or disappeared and why, your release-note findings for certifi and opentelemetry-api, and the make check summary lines.