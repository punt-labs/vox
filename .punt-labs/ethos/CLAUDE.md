# Ethos

How an agent drives ethos — not how to develop ethos itself. Ethos binds a
name, voice, email, GitHub handle, writing style, personality, and talents
into one identity that other tools read.

## Who am I

- `ethos whoami` — resolve your identity from the session, git config, or OS
  user.
- `ethos iam <persona>` — declare a persona for the current session.

Session hooks inject your persona into context at start and after
compaction. Ethos generates `.claude/agents/<handle>.md` from team data at
SessionStart; restart Claude Code to regenerate them after a team change.

## Delegation (missions)

- `ethos mission dispatch --worker <h> --evaluator <h> --write-set <paths> --criteria <text>`
  — write a mission contract. Dispatch writes the contract; a separate
  agent spawn does the work.
- `ethos mission show|log|results <id>` — inspect a mission.
- `ethos mission close <id>` — close a passing mission.
- `ethos mission pipeline list|show|instantiate <name>` — drive multi-stage
  work from a template.

Commit one logical step at a time; the write-set is enforced at runtime, so
an edit outside it fails the mission.

## Audit

- `ethos audit show --delegation <id>` — reconstruct a delegation's trail.
- `ethos audit seal` runs at pre-commit when ethos is enabled here; the
  sealed chunks travel in the same commit as the work.
- `ethos audit quarantine` — the recovery path for a corrupt chunk.

## Session

- `ethos session` — the current roster.
- `ethos session purge` — clear stale sessions.

## Gotchas

- Never run `make install` from inside Claude Code — the running binary
  cannot overwrite itself. Ask a human to run it from a shell.
- Agent types are discovered at SessionStart; restart after adding one.
- `ethos doctor` checks the seal hook only when ethos is enabled here — a
  dormant or never-enabled repo passes.
