# CLAUDE.md

## Project Overview

Text-to-speech CLI, MCP server, and Claude Code plugin. Supports ElevenLabs (premium), AWS Polly, and OpenAI TTS.

- **Package**: `punt-tts`
- **CLI**: `tts`
- **MCP server**: `tts-server`
- **Python**: 3.13+, managed with `uv`

## Build & Run

```bash
# Install with dev dependencies
uv sync --all-extras

# CLI
uv run tts --help
uv run tts doctor

# MCP server (stdio transport)
uv run tts-server
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

Module structure under `src/punt_tts/`:

| Module | Responsibility |
|--------|---------------|
| `types.py` | Domain types: `TTSProvider` protocol, `AudioProviderId`, `AudioRequest`, `AudioResult`, `HealthCheck`, `MergeStrategy` |
| `core.py` | `TTSClient` — provider-agnostic orchestration: batching, pair stitching, audio merge, `split_text()` |
| `output.py` | Output path resolution: `TTS_OUTPUT_DIR` env var, `~/tts-output` fallback |
| `logging_config.py` | Rotating file logging to `~/.punt-tts/logs/tts.log` |
| `ephemeral.py` | Ephemeral output mode: `.tts/` in cwd, auto-cleanup |
| `playback.py` | Serialized audio playback via `flock`: `play_audio()` (blocking), `enqueue()` (non-blocking detached) |
| `cli.py` | Click CLI — `--provider` flag, voice settings flags, synthesize, batch, pair, pair-batch, doctor, install, uninstall, install-desktop, play, serve |
| `installer.py` | Marketplace-based plugin install/uninstall: punt-labs marketplace registration, `claude plugin install/uninstall` |
| `server.py` | FastMCP server — MCP tools: `speak`, `chorus`, `duet`, `ensemble`, `set_config`. Reads/writes `.tts/config.md` for session vibe and plugin config. |
| `providers/__init__.py` | Provider registry, `get_provider()`, auto-detection (ElevenLabs > OpenAI > Polly) |
| `providers/polly.py` | `PollyProvider` — AWS Polly synthesis, voice resolution, health checks. Only file with boto3 |
| `providers/openai.py` | `OpenAIProvider` — OpenAI TTS synthesis, static voices, auto-chunking >4096 chars. Only file with openai |
| `providers/elevenlabs.py` | `ElevenLabsProvider` — ElevenLabs synthesis, voice settings, voice resolution, health checks. Only file with elevenlabs |

Plugin structure (Claude Code hooks and commands):

| Path | Responsibility |
|------|---------------|
| `hooks/hooks.json` | Hook registration: SessionStart, PostToolUse (tts tools + Bash), Stop, Notification |
| `hooks/state.sh` | Shared state reader and audio helpers for bash hooks (`enqueue_audio`, `play_audio_blocking`, `read_vibe_mode`, `read_vibe_signals`) |
| `hooks/notify.sh` | Stop hook: task-completion notification via decision-block pattern; includes auto-vibe signal data in block reason |
| `hooks/signal.sh` | PostToolUse hook (Bash): fast-gated signal accumulator for auto-vibe — appends `tests-pass`, `lint-fail`, etc. to `vibe_signals` |
| `hooks/notify-permission.sh` | Notification hook: async audio alerts for permission/idle prompts |
| `hooks/suppress-output.sh` | PostToolUse hook: formats MCP tool output for UI panel (includes `set_config` vibe-shift display) |
| `hooks/session-start.sh` | SessionStart hook: deploys commands, auto-allows MCP tools |
| `commands/notify.md` | `/notify y\|n` — toggle task notifications (uses `set_config` MCP tool) |
| `commands/speak.md` | `/speak y\|n` — toggle voice vs chime (uses `set_config` MCP tool) |
| `commands/recap.md` | `/recap` — on-demand spoken summary |
| `commands/say.md` | `/say <text>` — speak text aloud |
| `commands/voice.md` | `/voice on\|off\|status` — control voice mode (uses `set_config` MCP tool) |
| `commands/vibe.md` | `/vibe <mood>\|auto\|off` — session mood with auto-detection (uses `set_config` MCP tool) |
| `assets/chime_done.mp3` | Task-complete chime tone |
| `assets/chime_prompt.mp3` | Needs-approval chime tone |

Tests mirror source: `test_types.py`, `test_core.py`, `test_output.py`, `test_ephemeral.py`, `test_playback.py`, `test_cli.py`, `test_installer.py`, `test_server.py`, `test_polly_provider.py`, `test_openai_provider.py`, `test_elevenlabs_provider.py` plus `conftest.py` for shared fixtures.

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

Update `CHANGELOG.md` with every user-visible change. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Add entries under `[Unreleased]`. Categories: Added, Changed, Deprecated, Removed, Fixed, Security.

### Branch Discipline

All code changes go on feature branches. Never commit directly to main.

```bash
git checkout -b feat/short-description main
# ... work, commit, push ...
gh pr create --title "feat: description" --body "..."
# merge via PR, then delete branch
```

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

1. **Bump version** in `pyproject.toml`, `src/punt_tts/__init__.py`, and `.claude-plugin/plugin.json` (keep in sync)
2. **Move `[Unreleased]`** entries in `CHANGELOG.md` to new version section with date
3. **Run all quality gates** — ruff, mypy, pyright, pytest
4. **Commit**: `chore: release vX.Y.Z`
5. **Build locally**: `rm -rf dist/ && uv build && uvx twine check dist/*` (validation only — do NOT upload)
6. **Tag**: `git tag vX.Y.Z`
7. **Push**: `git push origin main vX.Y.Z` (triggers GH Actions release workflow)
8. **Wait for GH Actions**: `gh run watch` — workflow builds, publishes to TestPyPI, verifies install, then publishes to PyPI
9. **GitHub release**: `gh release create vX.Y.Z --title "vX.Y.Z" --notes-file -` (use CHANGELOG entry)
10. **Verify**: `uv tool install --force --refresh punt-tts==X.Y.Z && tts doctor`
11. **Restore editable**: `uv tool install --force --editable .` (for local dev)
12. **Marketplace**: bump both `version` and `source.ref` in `claude-plugins/.claude-plugin/marketplace.json`, PR + merge (ref MUST point to tag — see DES-015)

A release is not complete until all 12 steps are done. PyPI publishing is owned by GH Actions — never upload manually.

### Dev Plugin Testing

The plugin uses dev/prod namespace isolation. The working tree has `"name": "tts-dev"` in plugin.json, so it can run alongside the installed production plugin.

```bash
uv tool install --force --editable .   # Editable install (tts binary = working tree)
claude --plugin-dir .                   # Load dev plugin as tts-dev alongside prod tts
```

Dev commands (`/tts-dev:say-dev`, `/tts-dev:recap-dev`) use dev-namespaced MCP tools (`mcp__plugin_tts-dev_vox__*`). Prod commands (`/say`, `/recap`) continue using the installed plugin.

Release scripts swap the name before tagging:

- `bash scripts/release-plugin.sh` — swap `tts-dev` → `tts`, remove `*-dev.md`
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
