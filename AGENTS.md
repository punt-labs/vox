# Agent Instructions

> **Authoritative workflow is `CLAUDE.md` (noted 2026-07-03).** This file is a generic beads onboarding stub. vox requires **all changes to land via pull request; direct pushes to `main` are prohibited** (branch protection). Below, "push" means push your **feature branch** and open a PR — never push to `main`.

Read `.punt-labs/punt/CLAUDE.md` before planning or writing to the issue tracker. Linear is the tracker of record where this repo is migrated. Beads is a local mirror there and the tracker of record elsewhere.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:

   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```

5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
