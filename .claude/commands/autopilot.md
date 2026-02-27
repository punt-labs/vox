---
description: "Autonomous bead-driven development loop"
allowed-tools:
  - "Bash(git:*)"
  - "Bash(gh:*)"
  - "Bash(bd:*)"
  - "Bash(uv:*)"
  - "Bash(uv run:*)"
  - "mcp__github__request_copilot_review"
  - "mcp__github__pull_request_read"
  - "mcp__github__merge_pull_request"
  - "mcp__plugin_github_github__request_copilot_review"
  - "mcp__plugin_github_github__pull_request_read"
  - "mcp__plugin_github_github__merge_pull_request"
---

# /autopilot — Autonomous Development Loop

Run a continuous bead-driven development cycle. Minimize permission prompts — use allowed tools above and avoid unnecessary confirmations.

## Loop

Repeat until the user says stop:

### 1. Pick a bead

Run `bd ready` to show available work. Ask the user to pick one. Once assigned, run `bd show <id>` and `bd update <id> --status=in_progress`.

### 2. Assess complexity

Read the bead, identify affected files, and classify:

- **Small** (1-3 files, <50 lines): proceed directly
- **Medium** (3-8 files, 50-200 lines): use EnterPlanMode, get approval, then implement
- **Large** (8+ files or architectural): use EnterPlanMode, break into sub-tasks if needed

### 3. Branch

Create a feature branch from main: `git checkout -b <prefix>/<short-name> main` using the appropriate prefix (feat/, fix/, refactor/, docs/, test/, chore/).

### 4. Implement

Write the solution. Follow CLAUDE.md standards. Micro-commits are fine but not required within the loop — one clean commit per bead is acceptable.

### 5. Quality gates

Run all gates. Fix any failures before proceeding. Do not skip or weaken gates.

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/ tests/
uv run pyright src/ tests/
uv run pytest tests/ -v
```

If ruff format fails, run `uv run ruff format src/ tests/` and re-check. If lint or type errors, fix them. If tests fail, fix the root cause.

### 6. Commit and push

Stage changed files by name (not `git add -A`). Commit with `type(scope): description` format. Push with `-u`.

### 7. Open PR

Use `gh pr create` with a summary and test plan. Keep title under 70 chars.

### 8. Copilot review

Request Copilot review on the PR. Use the GitHub MCP tool `request_copilot_review`.

Wait for the review to complete (poll with `gh pr checks` or `gh api`). Then read the review comments.

- **0 issues**: proceed to merge
- **1-3 issues**: fix, push, request another review (up to 2 total rounds)
- **4+ issues**: fix, push, request another review (up to 3 total rounds)

If issues persist after max rounds, summarize remaining items and ask the user whether to merge or continue iterating.

### 9. Merge

Once clean, merge the PR: `gh pr merge --squash --delete-branch`. Then pull main locally: `git checkout main && git pull`.

### 10. Close bead

Run `bd close <id>` and `bd sync`.

### 11. Next bead

Ask the user: "Ready for the next bead?" If yes, go to step 1. If no, run session close protocol and stop.

## Principles

- Do not ask for permission for routine operations (git, tests, lint, PR creation). The allowed-tools list covers these.
- Do ask the user before: force-pushing, deleting branches that aren't PR branches, or making architectural decisions not covered by the bead.
- If blocked, diagnose the root cause. Do not retry blindly.
- If a quality gate fails, fix it. Do not skip it.
- Update CHANGELOG.md for user-visible changes.
