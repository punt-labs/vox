# CLAUDE.md

## No "Pre-existing" Excuse

There is no such thing as a "pre-existing" issue. If you see a problem — in code you wrote, code a reviewer flagged, or code you happen to be reading — you fix it. Do not classify issues as "pre-existing" to justify ignoring them. Do not suggest that something is "outside the scope of this change." If it is broken and you can see it, it is your problem now.

## Project Overview

Text-to-speech CLI, MCP server, and Claude Code plugin. Supports ElevenLabs (premium), AWS Polly, and OpenAI TTS.

- **Package**: `punt-vox`
- **CLI**: `vox`
- **MCP server**: `vox-server`
- **Python**: 3.13+, managed with `uv`

## Build & Run

```bash
# Install with dev dependencies
uv sync --all-extras

# CLI
uv run vox --help
uv run vox doctor

# MCP server (stdio transport)
uv run vox-server
```

## Scratch Files

Use `.tmp/` at the project root for scratch and temporary files — never `/tmp`. The `TMPDIR` environment variable is set via `.envrc` so that `tempfile` and subprocesses automatically use it. Contents are gitignored; only `.gitkeep` is tracked.

## Quality Gates

Run before every commit:

```bash
make check
```

## Architecture

Module structure under `src/punt_vox/`:

| Module | Responsibility |
|--------|---------------|
| `types.py` | Domain types: `TTSProvider` protocol, `AudioProviderId`, `AudioRequest`, `AudioResult`, `HealthCheck`, `MergeStrategy` |
| `core.py` | `TTSClient` — provider-agnostic orchestration: batching, pair stitching, audio merge, `split_text()` |
| `output.py` | Output path resolution: `VOX_OUTPUT_DIR` env var, `~/vox-output` fallback |
| `logging_config.py` | Logging setup (used by CLI and hooks, not by voxd) |
| `voxd.py` | **Audio daemon** (`voxd` binary). WebSocket server: synthesize, chime, record, voices, health. Playback queue, dedup, cache. System paths (Homebrew prefix on macOS, FHS on Linux). No MCP, no sessions, no project awareness. |
| `client.py` | WebSocket client for `voxd`. `VoxClient` (async), `VoxClientSync` (sync wrapper). Lightweight — stdlib + websockets only. |
| `config.py` | Read `.vox/config.md` YAML frontmatter: `read_field()`, `read_config()`, `write_field()`, `find_config()`. No ContextVar. |
| `resolve.py` | Shared resolution helpers: `resolve_voice_and_language()`, `apply_vibe()` |
| `normalize.py` | Text normalization for speech: `normalize_for_speech()` — snake_case, camelCase, abbreviation expansion |
| `voices.py` | Voice metadata: `VOICE_BLURBS`, `voice_not_found_message()` |
| `quips.py` | Centralized quip registry: all hook speech phrase pools as immutable tuples, grouped by event |
| `cache.py` | MP3 cache for quip phrases. Content-addressed by (text, voice, provider) via MD5. Runs inside `voxd`. |
| `hooks.py` | Hook handlers for Claude Code events. Call `voxd` via `VoxClientSync` for audio. `classify_signal()`, `resolve_tags_from_signals()`. |
| `__main__.py` | Typer CLI — unmute, record, vibe, on/off, mute, version, status, doctor, install, uninstall, play, mcp, daemon, hook, cache |
| `applet.py` | Lux display applet: builds element tree, connects to display server |
| `server.py` | FastMCP server (key: `mic`) — thin client of `voxd`. MCP tools: `unmute`, `record`, `vibe`, `who`, `notify`, `speak`, `status`, `show_vox`. Session state in memory (`SessionState` dataclass). |
| `service.py` | System-level daemon lifecycle. macOS: `/Library/LaunchDaemons/` (sudo). Linux: `/etc/systemd/system/` (sudo). Installs `voxd`. |
| `playback.py` | `play_audio()` — blocking audio playback via afplay/ffplay. Used by `voxd` internally. |
| `providers/__init__.py` | Provider registry, `get_provider()`, auto-detection (ElevenLabs > OpenAI > Polly) |
| `providers/polly.py` | `PollyProvider` — AWS Polly synthesis, voice resolution, health checks. Only file with boto3 |
| `providers/openai.py` | `OpenAIProvider` — OpenAI TTS synthesis, static voices, auto-chunking >4096 chars. Only file with openai |
| `providers/elevenlabs.py` | `ElevenLabsProvider` — ElevenLabs synthesis, voice settings, voice resolution, health checks. Only file with elevenlabs |
| `providers/say.py` | `SayProvider` — macOS `say` synthesis, voice resolution. Zero-config offline fallback |
| `providers/espeak.py` | `EspeakProvider` — Linux `espeak-ng` synthesis, voice resolution. Zero-config offline fallback |

Plugin structure (Claude Code hooks and commands):

| Path | Responsibility |
|------|---------------|
| `hooks/hooks.json` | Hook registration: SessionStart, PostToolUse (mic tools + Bash), Stop, Notification |
| `hooks/notify.sh` | Stop hook: thin gate → `vox hook stop` |
| `hooks/signal.sh` | PostToolUse hook (Bash): thin gate → `vox hook post-bash` |
| `hooks/notify-permission.sh` | Notification hook: thin gate → `vox hook notification` |
| `hooks/suppress-output.sh` | PostToolUse hook: formats MCP tool output for UI panel (self-contained bash) |
| `hooks/session-start.sh` | SessionStart hook: deploys commands, cleans retired commands, auto-allows MCP tools |
| `commands/vox.md` | `/vox y\|n\|c` — enable, disable, or continuous mode |
| `commands/unmute.md` | `/unmute [@voice]` — enable voice mode, set session voice, browse roster |
| `commands/mute.md` | `/mute` — chimes only |
| `commands/recap.md` | `/recap` — on-demand spoken summary (uses `unmute` MCP tool) |
| `commands/vibe.md` | `/vibe <mood>\|auto\|off` — session mood with auto-detection (uses `vibe` MCP tool) |
| `assets/chime_done.mp3` | Task-complete chime tone |
| `assets/chime_prompt.mp3` | Needs-approval chime tone |

Tests mirror source: `test_types.py`, `test_core.py`, `test_output.py`, `test_playback.py`, `test_cli.py`, `test_client.py`, `test_hooks.py`, `test_normalize.py`, `test_cache.py`, `test_keys.py`, `test_server.py`, `test_server_partition.py`, `test_service.py`, `test_applet.py`, `test_watcher.py`, `test_polly_provider.py`, `test_openai_provider.py`, `test_elevenlabs_provider.py`, `test_say_provider.py`, `test_espeak_provider.py` plus `conftest.py` for shared fixtures. See [TESTING.md](TESTING.md) for the full testing philosophy and architecture.

## Python Coding Standards

### Types

- `from __future__ import annotations` in every file.
- Full type annotations on every function signature and return type.
- mypy strict mode and pyright strict mode. Zero errors.
- Never `Any` unless interfacing with untyped libraries (pydub). Document why with inline ignores.
- `@dataclass(frozen=True)` for immutable value types.
- Use Protocol classes for abstractions. Never `hasattr()` or duck typing.
- `cast()` in string form for ruff TC006: `cast("list[str]", x)`.

### Exceptions and Error Handling

- Fail fast. Raise exceptions on invalid input. No defensive fallbacks.
- No warning filters to hide problems. Fix root causes.
- `ValueError` for domain violations. `click.ClickException` for CLI user errors.
- Never catch broad `Exception` unless re-raising or at a boundary (CLI entry point, MCP tool handler).

### Logging

- `logger = logging.getLogger(__name__)` per module.
- `logging.basicConfig()` configured once in CLI and server entry points.
- `logger.debug()` for synthesis details. `logger.info()` for file writes.
- MCP server logs to stderr only (stdout reserved for stdio transport).

### Imports and Style

- All imports at top of file, grouped per PEP 8 (stdlib → third-party → local).
- Double quotes. 88-character line limit. Enforced by ruff.
- No inline imports. No `type | None` parameters unless necessary.
- No backwards-compatibility shims. No `# removed` tombstones. No re-exports of dead symbols.

### Prohibited Patterns

- No `hasattr()` — use protocols.
- No mock objects in production code.
- No defensive coding or fallback logic unless explicitly requested.
- No `Any` without a documented reason and inline type-ignore comment.

## Testing

- **All tests must pass.** If a test is failing, fix it.
- If a test fails, fix it. Do not skip, ignore, or work around it.
- Mock Polly responses need valid MP3 bytes — pydub hands files to ffmpeg which rejects fake data. Use `AudioSegment.silent(duration=50)` in fixtures.
- Use `side_effect=lambda` instead of `return_value` for fresh mocks per call.
- Integration tests requiring AWS credentials are marked `@pytest.mark.integration`.

## Delegation with Missions

All code delegation uses ethos missions (`/mission` skill). Missions are typed contracts between a leader (claude) and a worker (bwk, rmh, mdm, adb, djb) that enforce write-set admission, frozen evaluators, bounded rounds, and append-only event logs.

### When to use missions

- Any bounded task with clear success criteria and a known set of files to touch.
- Sized for 1-3 rounds of one worker plus one evaluator.
- The Phase 3 runtime enforces the contract — write-set admission, frozen evaluator, result artifacts — instead of trusting prompt discipline.

Do NOT use missions for: exploratory research, work you do yourself, or epics that need decomposition first (decompose into multiple missions).

### Workflow

1. **Scaffold**: `/mission` skill scaffolds the contract YAML from conversation context.
2. **Confirm**: present the contract to the user (or decide as leader). Edit any field before creation.
3. **Create**: `ethos mission create --file .tmp/missions/<name>.yaml` — returns a mission ID.
4. **Spawn**: `Agent(subagent_type=<worker>, run_in_background=true)` with a prompt that points at the mission ID. The worker reads the contract via `ethos mission show <id>` as its first action.
5. **Track**: `ethos mission show <id>`, `ethos mission log <id>`, `ethos mission results <id>`.
6. **Review**: read the result artifact. Pass → `ethos mission close <id>`. Continue → `ethos mission reflect <id> --file <path>` then `ethos mission advance <id>`. Fail → `ethos mission close <id> --status failed`.

### Contract schema (required fields)

```yaml
leader: claude
worker: rmh                    # bwk|rmh|mdm|adb|djb|kpz
evaluator:
  handle: djb                  # must differ from worker, no shared role
inputs:
  bead: vox-0qi                # optional bead link
write_set:                     # repo-relative paths, at least one
  - src/punt_vox/music.py
  - tests/test_music.py
success_criteria:              # at least one verifiable criterion
  - vibe_to_prompt returns correct prompt for all test cases
  - make check passes
budget:
  rounds: 2                    # 1-10
  reflection_after_each: true  # leader reflects after each round
```

Optional: `context` (design notes), `tools` (worker allowlist), `session`, `repo`.
Do NOT set: `mission_id`, `status`, `created_at`, `evaluator.pinned_at`, `evaluator.hash`, `current_round` — the store manages these.

### Worker prompt template

```
Mission <id> is yours. Read it first: `ethos mission show <id>`.
The contract names the write set, success criteria, and budget.
Your first write must land inside the write set — the store
refuses anything else. After your work for this round, submit a
result artifact: `ethos mission result <id> --file <path>`. See
`ethos mission result --help` for the YAML shape. The mission
will refuse to close until a valid result for the current round
exists. Do not commit, push, or merge — return results to me.
```

### Evaluator defaults

| Task type | Evaluator |
|-----------|-----------|
| Security-sensitive code | `djb` |
| Go internals / library design | `mdm` or `bwk` |
| Python library design | `rmh` |
| CLI / developer experience | `mdm` |
| Infrastructure / CI | `adb` |

Worker and evaluator must be distinct handles with no shared role.

### Task tracking and parallelism

For multi-phase features (T1/T2), create a TaskCreate list with all missions up front and wire dependencies via `addBlockedBy`. This gives the user visibility into progress and lets you parallelize independent phases.

- **Create all tasks first**, then set dependencies. Mark phase 1 complete immediately if already done.
- **Launch independent missions in parallel** — if phases 2 and 3 don't depend on each other, create both missions and spawn both workers in the same message. Two `Agent()` calls, both `run_in_background: true`.
- **As each mission completes**: review the result, close the mission, commit, mark the task completed, check what's unblocked, and launch the next mission(s).
- **The task list is the source of truth** for what's done, what's in flight, and what's blocked. Update it as missions close.

### Scratch files

Mission contract YAMLs go in `.tmp/missions/`. Result artifact YAMLs go in `.tmp/missions/results/`.

## Issue Tracking with Beads

This project uses **beads** (`bd`) for issue tracking.

### When to Use Beads vs TodoWrite

| Use Beads (`bd`) | Use TodoWrite |
|------------------|---------------|
| Multi-session work | Single-session tasks |
| Work with dependencies | Simple linear execution |
| Discovered work to track | Immediate TODO items |
| Strategic planning | Tactical execution |

### Essential Commands

```bash
bd ready                    # Show issues ready to work
bd list --status=open       # All open issues
bd show <id>                # View issue details
bd update <id> --status=in_progress   # Claim work
bd close <id>               # Mark complete
bd create --title="..." --type=task   # Create issue
bd dep add <child> <parent> # child depends on parent
bd sync                     # Sync with git remote
```

## Development Workflow

### Changelog

CHANGELOG entries are written **in the PR branch, before merge** — not retroactively on main. The entry is part of the diff that gets reviewed. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Add entries under `[Unreleased]`. Categories: Added, Changed, Deprecated, Removed, Fixed, Security. See [Workflow standards §6](../punt-kit/standards/workflow.md) for full guidance.

### Branch Discipline

All code changes go on feature branches. Never commit directly to main.

**Use worktrees by default.** Before creating a branch, check `/who` for other active sessions. If other sessions are active, use a worktree to avoid interfering with their working tree. If no other sessions are active, a regular branch is fine.

```bash
# Default: worktree (safe when other sessions are active)
# Use the EnterWorktree tool, then work normally inside the worktree

# Alternative: regular branch (only when /who shows no other sessions)
git checkout -b feat/short-description main
# ... work, commit, push ...
gh pr create --title "feat: description" --body "..."
```

**One worktree per agent, many PRs within it.** A worktree is an isolated working directory for the session. Branch freely inside it — each PR gets its own branch, not its own worktree.

```bash
# Session start: enter worktree once
# EnterWorktree tool creates it

# PR 1
git checkout -b fix/thing-one main
# ... work, commit, push, PR, merge ...

# PR 2
git checkout main && git pull
git checkout -b feat/thing-two main
# ... work, commit, push, PR, merge ...

# Session end: /exit cleans up the worktree
```

**After creating a PR, watch CI and reviews without blocking your main shell.** Run `gh pr checks <number> --watch` in the background so you are notified when checks complete. Do not stop waiting — CI, Copilot, and Bugbot all need time to post.

```bash
gh pr checks <number> --watch          # Run in background task: polls until all checks resolve
```

**Expect 2-6 review cycles before merging.** Do not rush to merge after the first review. Each cycle: read feedback, fix, re-push, wait for new reviews. A PR is ready to merge ONLY when the most recent review cycle raised zero new issues — zero new comments, zero requested changes, all checks green.

**Read feedback using MCP GitHub tools.** Prefer MCP over `gh` CLI for all PR interactions:

```bash
# Primary: MCP tools (richer data, no local side effects)
mcp__github__pull_request_read  # method: get_reviews — review verdicts and bodies
mcp__github__pull_request_read  # method: get_review_comments — inline code comments

# Fallback only if MCP is unavailable
gh pr view <number> --comments
```

**Take every comment seriously.** There is no such thing as "pre-existing" or "unrelated to this change" — if you can see it, you own it. If a reviewer flags it, it matters. If you genuinely disagree, explain why in a reply — do not silently ignore. Copilot and Bugbot may take 1-3 minutes to post after CI completes; wait for them.

**Fix, re-push, and repeat.** After addressing feedback, push the fixes and wait for the next review cycle. Continue until the last cycle is uneventful — no new comments, no requested changes, all checks green. Only then is the PR ready to merge.

**Prefer MCP GitHub tools over `gh` CLI.** Use MCP tools for PR creation (`mcp__github__create_pull_request`), review reading (`mcp__github__pull_request_read`), and merging (`mcp__github__merge_pull_request`). MCP calls are API-only with no local git side effects. `gh pr merge` tries to checkout main locally, which fails inside a worktree.

**Merge via MCP, not `gh`.** Use `mcp__github__merge_pull_request` (API-only, no local git side effects). After merging, pull main to stay ready for the next PR.

```bash
# mcp__github__merge_pull_request(owner="punt-labs", repo="vox", pullNumber=N, merge_method="squash")
git fetch origin main && git checkout origin/main  # Detached HEAD (worktree can't checkout main branch)
git checkout -b feat/next-thing                     # New branch from latest main
```

**Worktree cleanup.** Never remove a worktree from inside it — the session cwd becomes invalid and unrecoverable. Let `/exit` handle cleanup. It prompts to keep or remove the worktree on session end.

| Prefix | Use |
|--------|-----|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `refactor/` | Code improvements |
| `docs/` | Documentation only |

### Micro-Commits

- One logical change per commit. 1-5 files, under 100 lines.
- Quality gates pass before every commit.
- Commit message format: `type(scope): description`

| Prefix | Use |
|--------|-----|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `refactor:` | Code change, no behavior change |
| `test:` | Adding or updating tests |
| `docs:` | Documentation |
| `chore:` | Build, dependencies, CI |

### Release Workflow

Use `/punt:auto release` (the slash command), which runs the `punt release` CLI
through the playbook executor with LLM-driven error diagnosis. It handles all
11 phases automatically: preflight, version bump, build, release PR, tag, CI wait,
GitHub release, PyPI verify, post-release (README SHA bump), cross-repo propagation
(install-all.sh, marketplace, website), and verification.

```text
/punt:auto release [version=X.Y.Z]
```

See [release-process.md](https://github.com/punt-labs/punt-kit/blob/main/standards/release-process.md) for
the full 11-phase specification. PyPI publishing is owned by the
tag-triggered `release.yml` workflow — never upload manually.

### Dev Plugin Testing

The plugin uses dev/prod namespace isolation. The working tree has `"name": "vox-dev"` in plugin.json, so it can run alongside the installed production plugin.

```bash
uv tool install --force --editable .   # Editable install (vox binary = working tree)
claude --plugin-dir .                   # Load dev plugin as vox-dev alongside prod vox
```

Dev commands (`/vox-dev:say-dev`, `/vox-dev:recap-dev`) use dev-namespaced MCP tools (`mcp__plugin_vox-dev_vox__*`). Prod commands (`/say`, `/recap`) continue using the installed plugin.

Release scripts swap the name before tagging:

- `bash scripts/release-plugin.sh` — swap `vox-dev` → `vox`, remove `*-dev.md`
- `bash scripts/restore-dev-plugin.sh` — restore dev state after tagging

### Session Close Protocol

Before ending any session:

```bash
git status                  # Check for uncommitted work
git add <files>             # Stage changes
git commit -m "..."         # Commit
bd sync                     # Sync beads
git push                    # Push to remote
git status                  # Must show "up to date with origin"
```

Work is NOT complete until `git push` succeeds.

## Pre-PR Checklist

- [ ] **CHANGELOG entry included in the PR diff** under `## [Unreleased]` (not retroactively on main)
- [ ] **README updated** if user-facing behavior changed (new commands, flags, providers, config)
- [ ] **prfaq.tex updated** if the change shifts product direction or validates/invalidates a risk
- [ ] **Quality gates pass** — `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pyright src/ tests/ && uv run pytest tests/ -v && shellcheck -x hooks/*.sh scripts/*.sh install.sh`

## Known Type Checker Workarounds

### mypy vs pyright on boto3 (in providers/polly.py)

boto3-stubs types `boto3.client("polly")` correctly for mypy but pyright sees partially unknown overloads. Solution:

```python
cast("PollyClientType", boto3.client("polly"))  # type: ignore[redundant-cast]  # pyright: ignore[reportUnknownMemberType]
```

### pydub and elevenlabs have no type stubs

Use `Any` annotations and pyright inline ignores. These are the acceptable `Any` usages. Both have `[[tool.mypy.overrides]]` with `ignore_missing_imports = true`.

## Standards

- Always find the root cause. No workarounds, no shortcuts.
- Do not suggest skipping tests, lowering standards, or ignoring failures.
- Do not present workarounds for failing tests — fix the actual problem.
- Report complete, unfiltered data.
- The user makes decisions. Ask before making up rationales.
