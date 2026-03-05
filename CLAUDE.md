# CLAUDE.md

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

## Quality Gates

Run after every code change. All must pass with zero violations.

```bash
uv run ruff check src/ tests/        # Lint
uv run ruff format --check src/ tests/ # Format check
uv run mypy src/ tests/               # Type check (strict)
uv run pyright src/ tests/            # Type check (strict)
uv run pytest tests/ -v               # All tests pass
shellcheck -x hooks/*.sh scripts/*.sh install.sh  # Shell lint
```

Build validation:

```bash
uv build
uvx twine check dist/*
```

## Architecture

Module structure under `src/punt_vox/`:

| Module | Responsibility |
|--------|---------------|
| `types.py` | Domain types: `TTSProvider` protocol, `AudioProviderId`, `AudioRequest`, `AudioResult`, `HealthCheck`, `MergeStrategy` |
| `core.py` | `TTSClient` — provider-agnostic orchestration: batching, pair stitching, audio merge, `split_text()` |
| `output.py` | Output path resolution: `VOX_OUTPUT_DIR` env var, `~/vox-output` fallback |
| `logging_config.py` | Rotating file logging to `~/.punt-vox/logs/tts.log` |
| `ephemeral.py` | Ephemeral output mode: `.vox/` in cwd, auto-cleanup |
| `playback.py` | Serialized audio playback via `flock`: `play_audio()` (blocking), `enqueue()` (non-blocking detached) |
| `config.py` | Centralized read/write for `.vox/config.md` YAML frontmatter: `read_field()`, `read_config()`, `write_field()`, `write_fields()`, `resolve_config_path()` |
| `resolve.py` | Shared resolution helpers: `resolve_voice_and_language()`, `resolve_output_dir()`, `apply_vibe()` |
| `voices.py` | Voice metadata: `VOICE_BLURBS`, `voice_not_found_message()` |
| `hooks.py` | Hook dispatchers for Claude Code events: `handle_stop()`, `handle_post_bash()`, `handle_notification()`, `classify_signal()`, `resolve_chime()` |
| `__main__.py` | Typer CLI — unmute, record, vibe, on/off, mute, version, status, doctor, install, uninstall, install-desktop, play, mcp, hook |
| `server.py` | FastMCP server (key: `mic`) — MCP tools: `unmute`, `record`, `vibe`, `who`. Reads `.vox/config.md` for session state. |
| `providers/__init__.py` | Provider registry, `get_provider()`, auto-detection (ElevenLabs > OpenAI > Polly) |
| `providers/polly.py` | `PollyProvider` — AWS Polly synthesis, voice resolution, health checks. Only file with boto3 |
| `providers/openai.py` | `OpenAIProvider` — OpenAI TTS synthesis, static voices, auto-chunking >4096 chars. Only file with openai |
| `providers/elevenlabs.py` | `ElevenLabsProvider` — ElevenLabs synthesis, voice settings, voice resolution, health checks. Only file with elevenlabs |

Plugin structure (Claude Code hooks and commands):

| Path | Responsibility |
|------|---------------|
| `hooks/hooks.json` | Hook registration: SessionStart, PostToolUse (mic tools + Bash), Stop, Notification |
| `hooks/notify.sh` | Stop hook: thin gate → `vox hook stop` |
| `hooks/signal.sh` | PostToolUse hook (Bash): thin gate → `vox hook post-bash` |
| `hooks/notify-permission.sh` | Notification hook: thin gate → `vox hook notification` |
| `hooks/suppress-output.sh` | PostToolUse hook: formats MCP tool output for UI panel (self-contained bash) |
| `hooks/session-start.sh` | SessionStart hook: deploys commands, cleans retired commands, auto-allows MCP tools |
| `commands/vox.md` | `/vox y\|n\|c` — set notification level: chimes, off, or continuous |
| `commands/unmute.md` | `/unmute [@voice]` — enable voice mode, set session voice, browse roster |
| `commands/mute.md` | `/mute` — chimes only |
| `commands/recap.md` | `/recap` — on-demand spoken summary (uses `unmute` MCP tool) |
| `commands/vibe.md` | `/vibe <mood>\|auto\|off` — session mood with auto-detection (uses `vibe` MCP tool) |
| `assets/chime_done.mp3` | Task-complete chime tone |
| `assets/chime_prompt.mp3` | Needs-approval chime tone |

Tests mirror source: `test_types.py`, `test_core.py`, `test_output.py`, `test_ephemeral.py`, `test_playback.py`, `test_cli.py`, `test_hooks.py`, `test_installer.py`, `test_server.py`, `test_polly_provider.py`, `test_openai_provider.py`, `test_elevenlabs_provider.py` plus `conftest.py` for shared fixtures.

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

- **All tests must pass.** No exceptions for "pre-existing failures."
- If a test fails, fix it. Do not skip, ignore, or work around it.
- Mock Polly responses need valid MP3 bytes — pydub hands files to ffmpeg which rejects fake data. Use `AudioSegment.silent(duration=50)` in fixtures.
- Use `side_effect=lambda` instead of `return_value` for fresh mocks per call.
- Integration tests requiring AWS credentials are marked `@pytest.mark.integration`.

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

**After creating a PR, block until CI and Copilot finish.** Do not proceed to merge or start other work until these commands complete. `--watch` blocks the shell until all checks resolve — this is intentional.

```bash
gh pr checks <number> --watch          # BLOCKING: polls until all checks pass or fail
gh pr view <number> --comments         # Read Copilot feedback — address before merging
```

**Merge via MCP, not `gh`.** Use `mcp__github__merge_pull_request` (API-only, no local git side effects). `gh pr merge` tries to checkout main locally, which fails inside a worktree. After merging, pull main to stay ready for the next PR.

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

Every release follows this exact sequence. No steps skipped.

1. **Bump version** in `pyproject.toml`, `src/punt_vox/__init__.py`, and `.claude-plugin/plugin.json` (keep in sync)
2. **Move `[Unreleased]`** entries in `CHANGELOG.md` to new version section with date
3. **Run all quality gates** — ruff, mypy, pyright, pytest
4. **Commit**: `chore: release vX.Y.Z`
5. **Build locally**: `rm -rf dist/ && uv build && uvx twine check dist/*` (validation only — do NOT upload)
6. **Tag**: `git tag vX.Y.Z`
7. **Push**: `git push origin main vX.Y.Z` (triggers GH Actions release workflow)
8. **Wait for GH Actions**: `gh run watch` — workflow builds, publishes to TestPyPI, verifies install, then publishes to PyPI
9. **GitHub release**: `gh release create vX.Y.Z --title "vX.Y.Z" --notes-file -` (use CHANGELOG entry)
10. **Verify**: `uv tool install --force --refresh punt-vox==X.Y.Z && vox doctor`
11. **Restore editable**: `uv tool install --force --editable .` (for local dev)
12. **Marketplace**: bump both `version` and `source.ref` in `claude-plugins/.claude-plugin/marketplace.json`, PR + merge (ref MUST point to tag — see DES-015)

A release is not complete until all 12 steps are done. PyPI publishing is owned by GH Actions — never upload manually.

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
