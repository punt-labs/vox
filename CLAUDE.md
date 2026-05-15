# Vox

Part of [Punt Labs](https://github.com/punt-labs). This repo must be checked out inside the `punt-labs/` workspace meta-repo so that org-wide configuration loads via Claude Code's ancestor directory walk:

- **`punt-labs/CLAUDE.md`** — org workflow, delegation model, beads issue tracking, tool configuration
- **`punt-labs/.claude/rules/python-*.md`** — 19 Python OO coding rules, scoped via `paths:` frontmatter (load on-demand when `.py` files are touched)
- **`punt-labs/.envrc`** — git identity, beads DB connection, API keys from platform keychain
- **`punt-kit/standards/`** — canonical reference docs

If cloned outside the workspace, these rules and configuration will not be present.

**OO Python standards adopted 2026-05-13.** The codebase does not yet fully comply. Every commit must improve OO scores (`make check-oo`), never regress. Do not match existing code patterns that violate the rules — write new code to the standard and improve touched files incrementally.

Text-to-speech CLI, MCP server, and Claude Code plugin. Supports ElevenLabs (premium), AWS Polly, and OpenAI TTS.

- **Package**: `punt-vox`
- **CLI**: `vox`
- **MCP server**: `vox-server`
- **Python**: 3.13+, managed with `uv`

## Architecture

### How synthesis works

An agent (or human) calls an MCP tool or CLI command requesting speech. The request flows through the thin client layer (`client.py` → WebSocket → `voxd`). The daemon selects a provider (ElevenLabs > OpenAI > Polly > platform fallback), synthesizes audio, optionally caches the result (content-addressed by text+voice+provider via MD5), and plays it through the system audio stack (afplay on macOS, ffplay on Linux). Long texts are split by `core.py` into sentence-boundary chunks and synthesized in parallel.

### Key architectural boundary: daemon vs. clients

**`voxd`** is the persistent audio daemon — it owns the playback queue, provider connections, cache, and system service lifecycle. It has no MCP awareness, no session state, no project context. **Clients** (`VoxClient`/`VoxClientSync`) are lightweight WebSocket wrappers. The MCP server (`server.py`) and hook handlers (`hooks.py`) are both thin clients of `voxd` — they translate MCP/hook semantics into daemon RPC calls. No business logic in the client layer.

This separation means: daemon bugs are about audio, providers, caching, and system paths. Client bugs are about session state, MCP protocol, and hook classification. They never overlap.

### Module map

| Module | Responsibility |
|--------|---------------|
| `types.py` | Domain types: `TTSProvider` protocol, `AudioProviderId`, `AudioRequest`, `AudioResult`, `HealthCheck`, `MergeStrategy` |
| `core.py` | `TTSClient` — provider-agnostic orchestration: batching, pair stitching, audio merge, `split_text()` |
| `voxd.py` | Audio daemon. WebSocket server: synthesize, chime, record, voices, health. Playback queue, dedup, cache. System paths (Homebrew on macOS, FHS on Linux). |
| `client.py` | WebSocket client for `voxd`. `VoxClient` (async), `VoxClientSync` (sync). Lightweight — stdlib + websockets only. |
| `config.py` | Two config files: `vox.md` (durable prefs) + `vox.local.md` (ephemeral state). Routes by `DURABLE_KEYS`/`EPHEMERAL_KEYS` frozensets. |
| `hooks.py` | Hook handlers for Claude Code events. Call `voxd` via `VoxClientSync`. `classify_signal()`, `resolve_tags_from_signals()`. |
| `server.py` | FastMCP server (key: `mic`) — thin client of `voxd`. Session state in memory (`SessionState` dataclass). |
| `providers/` | Provider registry + ElevenLabs, OpenAI, Polly, macOS `say`, Linux `espeak-ng`. Each provider is the only file importing its SDK. |
| `cache.py` | MP3 cache for quip phrases. Content-addressed by (text, voice, provider) via MD5. Runs inside `voxd`. |
| `service.py` | System daemon lifecycle. macOS: `/Library/LaunchDaemons/`. Linux: `/etc/systemd/system/`. |
| `normalize.py` | Text normalization for speech: snake_case, camelCase, abbreviation expansion |
| `quips.py` | Centralized quip registry: all hook speech phrase pools as immutable tuples |
| `applet.py` | Lux display applet: builds element tree, connects to display server |

### Plugin structure

| Path | Responsibility |
|------|---------------|
| `hooks/hooks.json` | Hook registration: SessionStart, PostToolUse (mic tools + Bash), Stop, Notification |
| `hooks/notify.sh` | Stop hook → `vox hook stop` |
| `hooks/signal.sh` | PostToolUse hook (Bash) → `vox hook post-bash` |
| `hooks/notify-permission.sh` | Notification hook → `vox hook notification` |
| `hooks/suppress-output.sh` | PostToolUse hook: formats MCP tool output for UI panel |
| `hooks/session-start.sh` | SessionStart: deploys commands, cleans retired commands, auto-allows MCP tools |
| `commands/` | `/vox`, `/unmute`, `/mute`, `/recap`, `/vibe` |

See `docs/architecture.tex` for the full system description.

## Code Quality

**OO ratchet:** `make check-oo` (part of `make check`) compares current OO scores against `.oo-baseline.json`. It passes only if no metric regressed on touched files and at least one metric improved. It fails if any metric got worse or nothing improved.

**Do not negotiate with the ratchet.** Do not edit `.oo-baseline.json` by hand except via `--rebaseline` for structural refactors. Do not suppress `check-oo`. If the ratchet fails, improve the code until it passes.

Workflow:

1. Write code that improves OO quality on the files you touch.
2. `make check` runs `check-oo --check` automatically. If it fails, fix the regression.
3. After all checks pass, run `make update-oo` to write the new baseline.
4. Stage `.oo-baseline.json` and `.oo-audit.jsonl` with your commit — they are committed files.

Bootstrap (first time only): run `make update-oo` to create the initial baseline.

- `make check-oo` — OO ratchet against baseline.
- `make update-oo` — update baseline and append to audit log after improvements.
- `make report` — full diagnostics including per-file OO breakdown.
- `make metrics` — ABC complexity analysis.
- `make coverage` — test coverage HTML report.
- `make check-coupling` — coupling/cohesion analysis (informational, not in check chain yet).

## Development Loop

Two nested loops govern all code changes.

### Inner loop — one mission

Execute after every agent delegation that produces code changes.

1. **Delegate** to the right ethos specialist. One mission = one focused task. Never batch multiple steps.
2. **`make check`** — must pass before proceeding. Zero exceptions.
3. **`/feature-dev:code-reviewer`** on the mission diff.
4. **`/pr-review-toolkit:silent-failure-hunter`** on the mission diff.
5. **Fix every finding.** Both agents must return zero findings. To dismiss one: document (a) the exact finding, (b) the specific reason it does not apply, (c) the code reference.
6. **Re-run both agents.** Exit the fix loop only when both return zero findings.
7. **Commit.**

### Outer loop — one PR (one rollback-coherent unit)

After all missions for the feature complete and each has passed its inner loop:

1. **`make check`** on the full accumulated diff.
2. **Both local review agents** on the complete diff.
3. **Fix all findings.** Re-run until clean.
4. **Push PR.**

### PR boundaries

Split by **rollback granularity**, not size. Ask: if this broke production, what reverts together? That is one PR. PRs should cover multiple steps — do not open a PR per step. Sequential steps in the same area belong in one PR.

**Known type checker workarounds:**

- **mypy vs pyright on boto3** (`providers/polly.py`): boto3-stubs types `boto3.client("polly")` correctly for mypy but pyright sees partially unknown overloads. Solution: `cast("PollyClientType", boto3.client("polly"))` with `# type: ignore[redundant-cast]` + `# pyright: ignore[reportUnknownMemberType]`.
- **pydub** has local type stubs in `typings/pydub/`. No `Any` annotations needed.
- **elevenlabs** ships `py.typed` but has pyright issues on some generated client code. Inline pyright ignores where needed.
- **`voxd` logs to stderr only in daemon mode.** Never use `print()` in server or daemon code.

## Testing

### Pyramid

| Layer | Make target | Runs in CI | What it covers |
|-------|-------------|------------|----------------|
| Unit | `make test` | yes | types, core, output, config, dirs, hooks, normalize, cache, keys, server, service, applet, all 5 providers |
| Integration | `@pytest.mark.integration` | no (needs AWS creds) | Real provider synthesis end-to-end |
| Shell scripts | `make lint` (shellcheck) | yes | hooks/*.sh, scripts/*.sh, install.sh |

Tests mirror source: one `test_*.py` per module plus `conftest.py` for shared fixtures. See [TESTING.md](TESTING.md) for the full testing philosophy and architecture.

### What good testing means in this project

Vox has five TTS providers, each with different SDKs, authentication, voice models, and error modes. The recurring failure mode is provider tests that pass with mocks but fail with real APIs because the mock doesn't match the SDK's actual behavior. Testing discipline:

- **Mock Polly responses need valid MP3 bytes.** pydub hands files to ffmpeg which rejects fake data. Use `AudioSegment.silent(duration=50)` in fixtures — never empty bytes or random data.
- **Use `side_effect=lambda` instead of `return_value`** for fresh mocks per call. `return_value` shares the same object across calls, causing aliasing bugs in tests that mutate results.
- **Every provider must test both success and auth failure paths.** A provider that can't authenticate should raise a clear error, not silently fall back.
- **Hook tests must verify signal classification.** The `classify_signal()` function in `hooks.py` determines what event type a Bash command represents. Misclassification means the wrong audio plays — test the classification logic explicitly.

## Ethos & Delegation

Identity: `agent: claude` per `.punt-labs/ethos.yaml`. All code delegation uses ethos missions. Every non-trivial delegation has two phases: (1) **design mission** — describes problem, constraints, and invariants but does NOT prescribe a write set; (2) **implementation mission** — uses the write set produced by the design phase. The design mission's output IS the write set — the specialist decides what to create, split, or extract. This is critical: prescribing a write set before design prevents refactoring and forces code into existing modules.

**One mission = one task.** Never give an agent multiple steps in a single prompt. Agent timeouts on multi-step prompts leave partial work and cause manual cleanup. One focused task per delegation.

**The COO does not write code.** Every code change, no matter how small, is delegated. There is no threshold below which a change is "too small to delegate." The only files the COO edits directly: `CHANGELOG.md`, `CLAUDE.md`, `DESIGN.md`, `README.md`, design docs, and plan files.

### Why these pairings

Vox spans three domains: (1) **audio/synthesis** — provider SDKs, audio formats, playback pipeline, daemon lifecycle — `rmh` for core Python, `gvr` for provider implementations, `kpz` for audio/playback performance; (2) **system integration** — launchd/systemd service, platform paths, hook scripts — `adb` for infrastructure, `djb` for auth/secrets, `mdm`/`rop` for shell/CLI; (3) **UX** — voice curation, vibe system, Lux applet — `claudia` for prose, `edt`/`dna` for visual.

| Task type | Worker | Evaluator |
|-----------|--------|-----------|
| Python core (`core.py`, `client.py`, `config.py`, `resolve.py`) | `rmh` (Hettinger) | `gvr` (van Rossum) |
| Provider implementations (Polly, OpenAI, ElevenLabs, say, espeak) | `gvr` | `rmh` |
| `voxd` audio daemon (WebSocket, queue, cache, playback) | `rmh` | `bwk` (Pike) |
| MCP server (`server.py`) tool surface | `rmh` | `mdm` (Pike) |
| CLI (`__main__.py`) command authoring | `mdm` | `rmh` |
| Hook scripts (`hooks/*.sh`) — bash, signal classification | `mdm` | `rop` (McIlroy) |
| System daemon install (`service.py`, launchd/systemd) | `adb` (Lovelace) | `djb` (Bernstein) |
| Provider auth / API-key handling / secrets | `djb` | `rmh` |
| Audio playback / pydub / ffmpeg pipeline | `gvr` | `kpz` (Karpathy) |
| Cache design (`cache.py`) — content-addressing, dedup | `rmh` | `kpz` |
| Voice / vibe / quip prose curation | `claudia` (Massimo) | `mcg` (Cagan) |
| Lux applet (`applet.py`) — visual surface | `edt` (Tufte) | `dna` (Norman) |
| Release / plugin name swap / cross-repo propagation | `adb` | `mdm` |
| Test infrastructure / fixtures (esp. mock MP3 bytes) | `rmh` | `gvr` |

### Pipeline selection

Use `standard` pipeline for new features, provider additions, daemon changes, or anything touching the hook/signal classification system. Use `quick` for single-module bugfixes that don't cross the daemon/client boundary. Review-cycle fix rounds (Copilot/Bugbot findings) use bare `Agent()`, not missions.

### Lessons from vox-0qi (2026-04-11)

- Write-set admission is the highest-value feature. It guaranteed parallel phases had zero file conflicts.
- Workers don't always read the contract via `ethos mission show` or submit results via `ethos mission result` — enforcement is partial. Verify the result artifact exists before closing.
- **Context/prompt split**: contract `context` carries the *what* (goal, constraints). Agent `prompt` carries the *how* (invocation instructions). Don't duplicate between the two.
- All 7 vox-0qi missions closed round 1. The reflect→advance cycle is untested under pressure.

See ethos docs for mission contract schema, worker prompt template, and the full mission lifecycle.

## Release

Use `/punt:auto release [version=X.Y.Z]`. Vox is a CLI + Plugin Hybrid — releases publish to both PyPI (`punt-vox`) and the marketplace.

### Dev plugin testing

The plugin uses dev/prod namespace isolation. Working tree has `"name": "vox-dev"` in plugin.json.

```bash
uv tool install --force --editable .   # Editable install (vox binary = working tree)
claude --plugin-dir .                   # Load dev plugin as vox-dev alongside prod vox
```

Release scripts: `scripts/release-plugin.sh` (swap `vox-dev` → `vox`), `scripts/restore-dev-plugin.sh` (restore dev state after tag).

## Key Documents

- `DESIGN.md` — ADR log
- `TESTING.md` — testing philosophy, fixture patterns, provider-specific test strategies
- `docs/architecture.tex` → `docs/architecture.pdf` — system architecture
- `prfaq.tex` → `prfaq.pdf` — product direction
- `docs/vox-notify.tex` — Z specification for notification system
