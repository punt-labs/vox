# Testing

The default suite needs no API keys, network, or audio hardware, but relies on local system binaries (ffmpeg for MP3 encoding, optionally espeak-ng). Opt-in roundtrip checks use a downloaded Whisper model; live integration checks call credentialed APIs.

## Local and CI feedback stages

Releases run the shared nightly suite against the built wheel before publishing
to TestPyPI, including Polly via OIDC and installed CLI/MCP checks. After the
TestPyPI install check, PyPI publication uses plain `release` environment approval.
The post-publish speech smoke installs the tagged version from PyPI in an isolated
environment and checks OpenAI speech against its transcription word for word.
It uses the offline oracle phrase, without brand words.

Run `make hooks` once after cloning. Hooks print their elapsed time, stop on the
first failure, and use the working tree for checks; stage the intended changes
before committing. `--no-verify` is for emergencies only, never a way to land
failing checks.

| Stage | Trigger | Commands | Budget |
| --- | --- | --- | --- |
| Pre-commit | Staged Python changes | Ruff format/check on staged files, `uv run mypy src/ tests/`, Pyright on staged files, `make test-changed` | <30s for a typical one-file change with warm type caches |
| Pre-push | Every local push | `make lint type test-push` | <3 min |
| Required PR checks | Pull request | Parallel `make coverage` (includes default, slow, subprocess and installed-wheel e2e), offline `make test-roundtrip`, packaging `make test-e2e`, no-drop coverage comparison | <10 min normally |
| Main verification | Push to main | Same CI suite; save successful coverage as `coverage-baseline-<sha>` | <10 min normally |

`make test` runs the default tier with `-n auto`. `make test-push` includes slow
and subprocess tests but excludes integration, SDK, e2e, roundtrip, and quarantine.
`make test-changed` selects changed test files and tests directly importing staged
`src/punt_vox` modules. Use `make test-changed REF=origin/main` to compare HEAD
against a merge base. Package `__init__.py` or `conftest.py` changes require the
whole default tier, which can exceed the pre-commit budget. Selection is a fast
direct-import heuristic, not a substitute for the full pre-push suite.

PR coverage compares against the exact merge-base SHA's cached report, never a
nearby commit. A cache miss reruns that base serially with the current coverage harness;
missing or invalid reports fail closed. Main publishes the baseline only after
successful coverage. Cold apt setup or a baseline cache miss can exceed the
normal CI budget; the test job has a 40-minute safety timeout to accommodate
both apt's 17-minute ceiling and the measured 14-minute serial fallback.

Initial measurements on an 8-vCPU Linux VM: default tier 587.38s serial versus
34.51s with auto workers (64.78s with four); slow/subprocess 31.77s serial versus
21.61s auto (18.97s with four). The parallel default runs exposed three
ambient-key-dependent tests, now given explicit fake keys. Warm mypy took 1.08s,
Pyright 20.54s, and Ruff check plus format check 0.20s. These are local timings,
not guarantees about GitHub runner capacity.

After implementation, the actual pre-commit hook on the single changed
`tests/test_elevenlabs_client.py` took **9.59s** (68 selected tests); the full
pre-push hook took **61.00s** (9,455 passed, four platform/tool skips). Both
measurements include their lint and type gates and use warm type caches.

Coverage of the same source tree was identical with serial and auto workers:
**97.00% lines**, **92.66% branches** (96.49% combined). The serial harness
(`uv run python tools/run_coverage.py --workers 0`) took **823.84s**; parallel
`make coverage`, including build/install, took **68.21s**. Both ran 9,461 tests
with four skips and retained installed-wheel subprocess coverage.

## Flake policy

A flaky test discovered on main or a PR must be fixed or quarantined **the same
day**. Quarantine requires a Linear issue and a named owner, with a deadline no
more than **14 days** away; record the owner and deadline in the issue and name
the owner in the mark's reason. Fix or delete the quarantined test by that
deadline. Review the quarantine list daily with
`uv run pytest --collect-only -m quarantine`.

Use `@pytest.mark.quarantine(reason="PL-1234: owner Jim")` (or the corresponding
Linear issue URL containing its `PL-` identifier). Collection rejects missing or untracked reasons, including inherited
marks. Default, pre-push, and coverage runs deselect quarantine; explicitly run
`uv run pytest -m quarantine` when investigating. Quarantine does not silently
retry a failure: never retry-until-green, and do not add rerun plugins.

## Philosophy

Vox has five TTS providers (ElevenLabs, OpenAI, Polly, macOS Say, espeak-ng), a plugin hook system, an MCP server, and a CLI. All of these talk to external services. The test suite proves correctness without ever calling them.

**Core principle**: mock at the provider boundary, test everything above it as real code.

## Architecture

Tests mirror source structure — one `test_*.py` per module — so the tree
below groups the suite by the area of the engine each file exercises. Files
prefixed `_` (`_cli_introspect.py`, `_program_fakes.py`) are shared helpers,
not test modules.

```text
tests/
  conftest.py                # Shared fixtures: mock clients, voice caches, valid MP3 bytes

  # Domain types, orchestration, text
  test_types.py              # Domain types re-exported from types.py
  test_types_synthesis.py    # SynthesisRequest / SynthesisResult / MergeStrategy
  test_types_health.py       # HealthCheck domain type
  test_core.py               # TTSClient orchestration: batching, SSML, stitching, merge
  test_synthesis_batch.py    # Parallel sentence-chunk batching
  test_chunked.py            # Chunked (long-text) synthesis splitting
  test_normalize.py          # Text normalization for speech
  test_resolve.py            # Leading expressive tags and vibe application
  test_voice_resolver.py     # Per-instance VoiceResolver cache load path
  test_provider_registry.py  # Provider registry selection order
  test_public_api.py         # __init__.py public library surface (__all__)

  # Output, playback, cache
  test_output.py             # Output path resolution
  test_output_formatter.py   # MCP tool-output formatting for the UI panel
  test_playback.py           # Audio playback (afplay/ffplay)
  test_cache.py              # Content-addressed MP3 quip cache

  # Config, state, paths, files
  test_config.py             # vox.md + vox.local.md durable/ephemeral routing
  test_config_isolation.py   # Config-dir isolation between repos/global
  test_frontmatter.py        # YAML frontmatter I/O: read/write/validate, OSError degradation
  test_markdown_doc.py       # Markdown-with-frontmatter document model
  test_private_state.py      # Ephemeral private-state file handling
  test_atomic_file.py        # Atomic file writes
  test_tool_owned_file.py    # Daemon-owned file ownership guard
  test_dirs.py               # Data/config directory resolution
  test_paths.py              # Path helpers (~/.punt-labs/vox, repo walk)
  test_path_status.py        # Path status reporting

  # Providers (mocked at the SDK boundary)
  test_polly_provider.py     # AWS Polly provider
  test_openai_provider.py    # OpenAI TTS provider
  test_elevenlabs_provider.py     # ElevenLabs TTS provider
  test_elevenlabs_music_provider.py  # ElevenLabs music provider
  test_say_provider.py       # macOS Say provider
  test_espeak_provider.py    # espeak-ng provider
  test_api_key_resolver.py   # API-key resolution across providers

  # WebSocket client for voxd
  test_client.py             # VoxClient / VoxClientSync
  test_client_gateway.py     # Client RPC gateway
  test_client_catalog_gateway.py  # Catalog (album) client gateway
  test_wire_reply.py         # Wire reply parsing

  # Claude Code hooks
  test_hooks.py              # Hook dispatchers: stop, vibe-nudge, notification
  test_hook_envelope.py      # Hook event envelope parsing
  test_hook_gate.py          # Hook enable/disable gating
  test_hook_payload.py       # Hook payload extraction
  test_hook_scripts.py       # plugin/hooks/*.sh shell entry points
  test_nudge_hook.py         # Vibe-nudge hook
  test_vibe_nudge.py         # Vibe-nudge cadence: threshold fire, reset, auto-only gating
  test_audible_notify.py     # Audible notification gating
  test_suppress_output.py    # PostToolUse output suppression
  test_control.py            # Control-action (zero-text) handling
  test_deposited_files.py    # Repo guide deposit + stamp verification
  test_claude_md.py          # CLAUDE.md @-import management

  # MCP server (mic)
  test_server.py             # Tools: unmute, vibe, who, notify, speak, status
  test_server_partition.py   # Z-spec partition tests for MCP state transitions
  test_server_music_tool.py  # music tool (subcommand dispatch)
  test_server_rec_music.py   # rec + music tool interaction
  test_server_enablement.py  # enablement tool (enable/disable)

  # CLI
  test_cli.py                # Typer CLI invocations via CliRunner
  test_cli_io.py             # CLI stdin/stdout plumbing
  test_cli_music.py          # vox music subcommands
  test_cli_rec.py            # vox rec subcommands
  test_cli_enablement.py     # vox enable / disable

  # Per-repo enablement & install
  test_enablement.py         # Enablement marker + import + settings
  test_settings_registration.py   # settings.json registration
  test_desktop_install.py    # Desktop (.mcpb) install
  test_install_script.py     # install.sh behavior

  # System service (launchd / systemd)
  test_service_health.py     # Service health check
  test_service_health_verify.py   # Health-verify probe
  test_service_installer.py  # Install/uninstall orchestration
  test_service_keys_env.py   # keys.env for the service
  test_service_launchctl.py  # macOS launchctl calls
  test_service_launchd.py    # launchd plist generation
  test_service_process.py    # Process lifecycle
  test_service_systemd.py    # systemd unit generation
  test_service_types.py      # Service domain types
  test_keys.py               # keys.env parsing and loading
  test_sibling_lock.py       # Sibling-process lock
  test_daemon_restarter.py   # Daemon restart logic
  test_doctor.py             # vox doctor diagnostics

  # voxd daemon internals
  test_voxd_daemon.py        # Daemon lifecycle
  test_voxd_router.py        # WebSocket request routing
  test_voxd_parse.py         # Wire request parsing
  test_voxd_wire_text.py     # Wire text handling
  test_voxd_wire_fault.py    # Wire fault handling
  test_voxd_synthesis.py     # Daemon-side synthesis
  test_voxd_speech_handlers.py    # Speech handlers
  test_voxd_play_handler.py  # Play handler
  test_voxd_playback.py      # Playback queue
  test_voxd_fetch_handler.py # Fetch handler
  test_voxd_chunked_fetch.py # Chunked fetch
  test_voxd_cache_handlers.py     # Cache handlers
  test_voxd_dedup.py         # Playback dedup
  test_voxd_chimes.py        # Chime handlers
  test_voxd_record_handler.py     # Record handler
  test_voxd_record_store.py  # Recording store
  test_voxd_rec_handlers.py  # rec subcommand handlers
  test_voxd_health.py        # Daemon health endpoint
  test_voxd_config.py        # Daemon config
  test_voxd_containment.py   # Path containment guard
  test_voxd_data_root_boundary.py # Data-root boundary guard
  test_voxd_system_handlers.py    # System (voices, etc.) handlers
  test_voxd_audit_log_level.py    # Audit log level
  test_voxd_log_level_handler.py  # Log-level handler

  # Logging
  test_logging_config.py     # Shared logging adapter and private sink policy
  test_logging_entrypoints.py # Entry-point roles
  test_logging_sources.py    # Logging source-policy gate
  test_log_sanitize.py       # Log sanitization (secret redaction)
  test_crash_logging.py      # Crash logging

  # Vibe, music phrases
  test_vibe.py               # Vibe state
  test_vibe_command.py       # Vibe command
  test_vibe_label.py         # Vibe label
  test_vibe_trace.py         # Vibe trace logging
  test_music_args.py         # Music argument parsing
  test_music_hint.py         # Music hint phrases
  test_music_phrases.py      # Music phrase pools
  test_music_prompts.py      # Music prompt building

  music_player/              # Lux music-player surface (voxd/music_player/)
    conftest.py
    test_player.py, test_player_view.py, test_player_events.py
    test_scene.py, test_scene_mailbox.py, test_lux_scene_publisher.py
    test_lux_clients.py, test_lux_menu.py, test_lux_subscription.py
    test_lux_trace.py, test_composition.py, test_publish_button.py
    test_album_row.py, test_transport_row.py, test_playback_notice.py

  programs/                  # Music/background-audio programs (voxd/programs/)
    conftest.py
    test_program.py, test_state.py, test_mode.py, test_mode_transition_log.py
    test_loop.py, test_filler.py, test_fill_guard.py, test_fill_reconciler.py
    test_fill_recorder.py, test_fill_outcome.py, test_fill_signal.py
    test_producer.py, test_player.py, test_subprocess_player.py
    test_playback_policy.py, test_playback_source.py, test_playback_health.py
    test_rotate_policy.py, test_retry_machine.py, test_selection.py
    test_selection_playback.py, test_select_handler.py, test_select_signal.py
    test_catalog.py, test_library.py, test_library_handlers.py, test_store.py
    test_album_contents.py, test_album_id.py, test_album_tags.py
    test_manifest.py, test_part.py, test_part_tags.py, test_format.py
    test_control_channel.py, test_control_signal.py, test_change_signal.py
    test_switch_signal.py, test_interrupt_race.py, test_suspension.py
    test_active_context.py, test_status.py, test_wire.py, test_wiring.py
    test_handlers.py, test_service.py, test_sleeper.py, test_guard.py
    test_hex_token.py, test_identifiers.py, test_invariants.py
    test_properties.py
```

## Testing Pyramid

Vox has no NATS/relay/multi-user surface, so this is not biff's eight-tier
pyramid. Vox's actual shape is five TTS providers (mocked at the SDK
boundary), a `voxd` daemon over WebSocket, an MCP server, a CLI, Claude Code
hook shell scripts, a Lux display applet, and a `voice_call` subpackage
that streams audio to and from ElevenLabs and delegates to workers.
The tiers below reflect that shape, not a copy of biff's.

| Tier / marker | What it covers | How to run | Runs in CI? | Current count |
|---------------|----------------|------------|-------------|---------------|
| Default/unmarked | Unit and mocked in-process cross-component tests; no live services or credentials | `make test` | Yes, combined coverage job | 8,058 |
| `subprocess` | Real local shell and Python process tests | `uv run pytest -m subprocess` | Yes, combined coverage job | 157 |
| `slow` (cross-cutting) | Heavy local process/filesystem overhead; currently concurrent logging-process rotation | `uv run pytest -m slow` | Yes, combined coverage job | 1 |
| `e2e` | Installed-wheel CLI and MCP packaging smoke, not live audio | `make install` then `make test-e2e` | Yes, combined coverage and separate `packaging-e2e` jobs | 10 |
| `roundtrip` | Real TTS → Whisper/Scribe WER, cloud credential rejection, and agent speech | See [Integration Tests](#integration-tests) | Offline PR/push; live nightly/manual | 40 |
| `playback` | Real daemon → virtual device monitor → Whisper, including live chunk seam | `make test-playback-capture` / `make test-playback-capture-live` | Nightly/manual | 3 |
| `levels` | Chime/music loudness, silence, track change via ffmpeg + mpv; live music also `integration` | `make test-levels` / `make test-levels-live` | Nightly/manual | 20 |
| `integration` | Live provider speech and credential rejection, music levels, and playback capture | See [Integration Tests](#integration-tests) | Nightly/manual | 37 |
| `agent` | Paid headless Claude Code → vox plugin → recorded speech → Whisper (also `roundtrip`) | `make test-agent` | Nightly/manual | 1 |
| `sdk` | Real paid Claude Agent SDK session; reserved/empty | `uv run pytest -m sdk` | No | 0 |
| Live audio (no marker) | Manual, human-judged audio quality; not `e2e` | Follow `docs/testing/manual-tests.md` | No | N/A (manual) |

Counts are collected test cases as of this revision, not execution results:
8,058 default / 8,290 collected across all markers. Cross-cutting markers
overlap, so the counts are not additive.

### Default/unmarked: unit and mocked cross-component tests

No marker, no external service dependency. This is the vast majority of the
suite (9,305 of 9,593 collected tests as of this revision) and runs on every `uv run pytest`, `make test`,
and `make check`. A handful of unmarked tests still spawn real local
subprocesses -- `test_core.py` invokes `ffmpeg` directly outside the
cached-MP3-bytes fixture path, and `test_public_api.py` runs a `python -c`
import-lightness probe -- but none reach the network or a credentialed API, so
the tier's contract ("safe to run anywhere, no external service dependency") holds. Tests that spawn a real *shell script* under test move to
the `subprocess` tier; local `ffmpeg`/interpreter spawns from tests whose subject is
in-process code stay here.

| What it covers | Representative files |
|-----------------|----------------------|
| Pure functions, domain types | `test_types*.py`, `test_normalize.py`, `test_resolve.py` |
| Mocked-provider-boundary synthesis | `test_polly_provider.py`, `test_openai_provider.py`, `test_elevenlabs_provider.py`, `test_say_provider.py`, `test_espeak_provider.py`, `test_core.py` |
| Real pydub/ffmpeg encode, mocked network | `test_chunked.py`, `test_core.py`, `test_public_api.py` (see [The MP3 Problem](#the-mp3-problem) -- within the mocked-provider fixture path the one real ffmpeg encode is cached once per session, not per test; tests that call `ffmpeg` directly, outside that fixture path, pay their own per-invocation cost) |
| WebSocket client/daemon wire protocol (in-process) | `test_client*.py`, `test_wire_reply.py`, `test_voxd_*.py` |
| Hook dispatch logic (config/audio patched) | `test_hooks.py`, `test_hook_envelope.py`, `test_hook_payload.py`, `test_nudge_hook.py` |
| Static shell-script assertions (no subprocess) | `test_hook_scripts.py` -- reads and greps the `.sh` files as text; does not execute them |
| Filesystem/config I/O against `tmp_path` | `test_config.py`, `test_frontmatter.py`, `test_atomic_file.py` |
| Service lifecycle, subprocess calls mocked via `@patch` | `test_service_process.py`, `test_service_launchctl.py`, `test_service_systemd.py` -- these spawn zero real processes; `subprocess.run` is patched at every call site |
| Z-spec partition tests | `test_server_partition.py` |
| Lux music-player / audio-programs subsystems (mocked Lux client) | `tests/music_player/`, `tests/programs/` -- in-memory/mocked |

### OO, coupling, and suppression ratchets

`make design-shadow` reports advisory design changes against `origin/main`
(`DESIGN_BASE` overrides it). It is not part of `make check`. PR CI publishes
the verdict to its job summary, even after another gate fails, without gating.
The layer map in `pyproject.toml` separates entry wiring, concrete adapters,
application orchestration and domain models; findings remain advisory.

The dev dependency `punt-oo-ratchet==0.4.0` supplies all three gates through
`oo-ratchet {oo,coupling,suppressions}`. Their implementation tests live in
[`python-oo-ratchet`](https://github.com/punt-labs/python-oo-ratchet), not in
this repository. Run `make check-oo check-coupling check-suppressions` to
check vox against its existing committed baselines and audit logs.

### `subprocess` -- real local process tests

`@pytest.mark.subprocess` is applied to the files that can only be honest by
spawning a real process to exercise scripts and their process boundaries:

| File | What it drives |
|------|----------------|
| `test_hook_gate.py` | Real `bash plugin/hooks/*.sh` invocations |
| `test_plugin_surface.py` | Real plugin-surface gate against fixture surfaces |
| `test_install_script.py` | Real `sh install.sh` under a sandboxed `PATH` |
| `test_suppress_output.py` | Real PostToolUse output script; requires `jq` |
| `test_logging_sources.py` | Real Python logging-source gate against fixture trees |
| `test_session_start_hook.py` | Real session-start command deployment; requires `jq` |
| `test_session_start_pid.py` | Real session-start process-tree PID resolution |

`test_hook_scripts.py` looks like it belongs in this tier by name but does
not: it makes static text assertions over the `.sh` files (`grep`-shaped
checks for dead references), imports no `subprocess`, and spawns nothing. It
stays in the default tier.

The default tier exercises call lifecycle, audio routing and worker tools
with test doubles at the provider and audio boundaries. The
[README walkthrough](README.md#walkthrough) uses the real service.

### `integration`, `agent`, and `sdk`

`integration` marks live provider checks, not mocked cross-component wiring.
The shared nightly/manual suite fails when prerequisites are missing;
local runs report and skip missing credentials.

`agent` exercises the real Claude Code CLI and the plugin's MCP server, not
the Claude Agent SDK. It is excluded from default, PR, and offline roundtrip
runs. It does not replace playback capture or manual listening.

`sdk` is reserved for a real paid Claude Agent SDK session and is not run in
CI.

### `slow` -- cross-cutting overhead

The current `slow` test exercises concurrent logging-process rotation in
`test_logging_config.py`. Ratchet implementation tests now live in
`python-oo-ratchet`, not in this suite.

### `e2e` -- installed-wheel packaging

Run `make install` then `make test-e2e` to drive the installed `vox` CLI
and `vox mcp` over stdio. This checks packaging and startup, not human-judged
audio quality. It runs in the combined coverage job and its own packaging CI
job, and is excluded from the default tier.

## Suite Runtime

The default tier was observed taking **~12.5 minutes on a Devin VM**.
This is a machine-specific observation, not a CI timing guarantee or a new
measurement made during this documentation update.

The previous 2:52 benchmark described a smaller historical suite. Its
ratchet-test breakdown and CPU-contention hypothesis do not establish the
cause of the current runtime. Profile the current suite before attributing
its cost to a particular component or to machine contention.

`pytest-xdist` is not a declared dependency; the default command does not
request parallel workers.

## The MP3 Problem

pydub hands audio files to ffmpeg, which validates MP3 headers. Mocks that return `b"fake mp3"` cause ffmpeg to reject the data, producing confusing failures far from the mock site.

Solution: `conftest.py` generates real MP3 bytes once via `AudioSegment.silent(duration=50)`, caches the result, and every mock provider response uses these bytes. This means stitching, merging, and file-write tests exercise real pydub/ffmpeg codepaths.

```python
# conftest.py — cached valid MP3 generation
def _generate_valid_mp3_bytes() -> bytes:
    silence = AudioSegment.silent(duration=50)
    buf = io.BytesIO()
    silence.export(buf, format="mp3")
    return buf.getvalue()
```

## Voice Cache Isolation

Provider voice caches are **per-instance**, not module-level. Each provider holds its own `self._voices = VoiceResolver(...)` (populated lazily on first use by calling the provider API); the OO refactor (Phase F, PR #264) moved these off the old module-level `VOICES` dict + `_voices_loaded` globals. Because the cache lives on the instance, constructing a fresh provider (or mock-backed provider) per test gives natural isolation — there is no module-level global to reset, and the old `autouse` `_populate_*_voice_cache` fixtures are gone.

Tests that verify cache-loading behavior itself (e.g., "does `resolve` call the API when the cache is empty?") construct a `VoiceResolver`/provider with an empty cache and assert the load path fires. (OpenAI keeps a static `VOICES` name→id constant map — that is a fixed lookup table, not a runtime API cache, so it needs no reset.)

## Mock Boundaries

Each provider fixture injects a mock client at construction time:

| Fixture | What's mocked | What's real |
|---------|--------------|-------------|
| `polly_provider` | `boto3.client("polly")` | `PollyProvider`, voice resolution, SSML generation |
| `openai_provider` | `openai.OpenAI()` | `OpenAIProvider`, chunking (4096 char limit), voice mapping |
| `elevenlabs_provider` | `elevenlabs.ElevenLabs()` | `ElevenLabsProvider`, voice resolution, streaming reassembly |
| `say_provider` | `platform.system()`, `shutil.which()` | `SayProvider`, voice resolution, command building |
| `espeak_provider` | `_find_espeak_binary()` | `EspeakProvider`, voice resolution, argument construction |
| `tts_client` | Only the provider (via `polly_provider`) | `TTSClient` orchestration: batching, stitching, merge strategies |

Mock responses use `side_effect=lambda` instead of `return_value` so each call gets a fresh response object. This prevents shared mutable state between assertions.

## Hook Testing

Hook tests (`test_hooks.py`) verify the Claude Code plugin integration — stop hooks, the vibe-nudge cadence, and notification dispatch. These mock at two boundaries:

1. **Config I/O** — `ConfigStore.write_field` / `write_fields` are patched to avoid filesystem interaction (and to inject `OSError` for the counter-persist-failure path)
2. **Audio dispatch** — `_chime_via_voxd` and `_speak_via_voxd` are patched to prevent actual playback

The `_make_config()` helper constructs `VoxConfig` objects directly, bypassing file parsing. This isolates hook logic from config parsing logic (which has its own tests in `test_config.py`).

Key patterns tested:

- Stop hook returns only a `♪` phrase with no internal data
- The vibe-nudge hook fires the reminder only on the Nth auto-mode prompt, then resets the counter
- Below the threshold, and in manual/off mode, the nudge emits nothing
- On a counter-persist failure the nudge stays silent (no reminder) rather than firing every prompt
- The nudge is non-blocking (never emits a `decision`) and synchronous

## Server Testing

Server tests (`test_server.py`) exercise the MCP tool functions directly —
`unmute()`, `vibe()`, `who()`, `notify()`, `speak()`, and `status()`. The
three subcommand-dispatched tools have their own files: `rec` (recordings) and
`music` (background program) in `test_server_rec_music.py` and
`test_server_music_tool.py`, and `enablement` (enable/disable) in
`test_server_enablement.py`. A `_patch_config` fixture creates a temp config
file and monkeypatches the module-level config path, so tools read/write real
YAML frontmatter in an isolated temp directory.

Provider construction is patched (`get_provider`, `TTSClient`) so no API clients are created. The tests verify argument threading (voice, language, rate, vibe tags), config side effects, and error messages.

## CLI Testing

CLI tests (`test_cli.py`) use Typer's `CliRunner` to invoke commands as subprocesses would. Provider construction and audio playback are patched. Tests verify exit codes, stdout/stderr output, and argument parsing.

## Config Testing

Config tests (`test_config.py`) use `tmp_path` fixtures to create the real
split config — `vox.md` (durable prefs) and `vox.local.md` (ephemeral state) —
inside a repo's `.punt-labs/vox/` directory, each with YAML frontmatter. Tests
verify:

- Durable/ephemeral routing: a field is written to `vox.md` or `vox.local.md`
  by its membership in `DURABLE_KEYS` / `EPHEMERAL_KEYS`
- Field reading with and without quotes
- Field writing (insert, update, multi-field atomic writes)
- Key validation (unknown keys raise `ValueError`)
- Edge cases: missing file, empty frontmatter, no frontmatter delimiters

## Markers

The [named-tier table](#testing-pyramid) lists the core markers, their commands,
CI coverage, and collected count. The default selection excludes `slow`,
`subprocess`, `e2e`, `integration`, `sdk`, `roundtrip`, and `playback`. An explicit `-m`
expression overrides that selection. Live audio has no pytest marker.

## Running Tests

```bash
# Default: no live services or credentials; some local processes
uv run pytest

# Single file
uv run pytest tests/test_hooks.py -v

# Single test
uv run pytest tests/test_hooks.py::TestHandleStop::test_voice_mode_blocks_clean_reason -v

# slow -- heavy local process/filesystem overhead
uv run pytest -m slow

# subprocess -- real shell-script/install.sh subprocess tests
uv run pytest -m subprocess

# All local tiers except the separately run speech oracle and signal levels
uv run pytest -m "not integration and not sdk and not roundtrip and not levels"

# Packaging e2e separately, against the installed wheel
make install
make test-e2e

# All CI-capable tiers with line and branch coverage (builds/installs the wheel)
make coverage
```

## Integration Tests

### Stage five: playback capture

`make test-playback-capture` selects `playback and not integration`, so it never
spends provider quota even when credentials are present. Run
`make test-playback-capture-live` for `playback and integration`; the live Polly
and ElevenLabs cases carry both markers. CI explicitly selects `-m playback`
to run both offline and live cases. Playback is excluded from default tests
and combined coverage. A private headless PulseAudio null sink records
its monitor while a real isolated `voxd` subprocess speaks. Pinned CPU/int8
Whisper small.en transcribes that capture; WER must be at most 0.05 and unique
marker words must occur exactly once in order. Install `pulseaudio`,
`pulseaudio-utils`, `ffmpeg`, and `espeak-ng`, then sync the `roundtrip` group.

The offline case covers sequential espeak direct-device requests. Neither
espeak record nor Polly synthesis chunks text, so no offline chunking claim is
made. Live Polly covers file synthesis and ffplay queue playback. Live
ElevenLabs uses just over 5,000 characters with `eleven_v3`, forcing two real
provider chunks with markers on opposite sides of the seam. Missing provider
credentials or audio prerequisites skip locally; `VOX_PLAYBACK_REQUIRE=1`
makes them failures in the nightly/manual `roundtrip-live.yml` playback job.
Live cases need `ELEVENLABS_API_KEY` and the same `AWS_*` variables as roundtrip.
Concurrent submission order is not asserted: FIFO applies to enqueued files,
not to synthesis completion across independent client connections.

Speech intelligibility checks live in `tests/roundtrip/`. Run
`make test-roundtrip` for real espeak-ng en-us synthesis at 131 WPM and
pinned deterministic CPU/int8 Whisper small.en transcription. The first model
download needs network; recognition is then offline. The model cache is resolved
before test HOME isolation; both CI workflows set and cache the same absolute
`HF_HUB_CACHE`. Normalization, splitting
at a 240-character budget, and stitching run through vox's real `TTSClient`.
The oracle rejects silent/empty decoded audio and WER above **0.02** offline
(measured 0, 0, 0, 1/74 across four passages; margin 0.0065), or 0.05 for live
TTS. One wrong word in the shortest reference costs 0.10, above the limit.
Real synthesized drop, swap, identity-normalizer, and wrong-text faults must
all be rejected. Identifier references are independent of the normalizer.

Run `make test-roundtrip-live` for ElevenLabs/OpenAI/Polly TTS crossed with
Whisper/Scribe (also marked `integration`). Missing credentials produce named
skips unless `VOX_ROUNDTRIP_REQUIRE_LIVE=1`, as in nightly CI. ElevenLabs and
Scribe use `ELEVENLABS_API_KEY`; OpenAI uses `OPENAI_API_KEY`. For Polly on a
dev box, export temporary AWS SSO credentials and set `AWS_DEFAULT_REGION`.

Run `make test-roundtrip-auth` to verify that the three cloud providers reject
deliberately invalid credentials without writing audio. The test contacts the
real endpoints without valid keys. Run `make test-roundtrip-say` on macOS for
native Say → Whisper. It needs `say` and `ffmpeg`, not cloud credentials. Say
and espeak-ng have no authentication path. These commands do not run by default.

Signal-level checks live in `tests/levels/` and judge what speech-to-text
cannot: loudness, silence, and the change between music tracks. ffmpeg's
`ebur128` (BS.1770 integrated loudness, true peak) and `silencedetect`
(-50 dBFS) are the oracle. `make test-levels` needs ffmpeg and mpv, no keys:
every bundled chime sounds within 20 ms, lasts 0.2-1.5 s, sits at -21 to
-13 LUFS with true peak at or below -1 dBTP, and chimes differ by at most
3 LU (measured: done -16.3 LUFS / -10.4 dBTP, prompt -18.5 / -12.4). Late,
quiet, clipped, and truncated chimes must be rejected. Track change is
rendered through mpv with voxd's player settings (`--gapless-audio=yes`,
`--volume=60`, no crossfade) and passes with at most 1.0 s of silence at the
boundary and at most 6 LU between the two tracks' integrated loudness;
synthetic tones prove gaps and jumps fail. `make test-levels-live` generates two 20 s ElevenLabs tracks, edge-trimmed as voxd
stores them (`EdgeTrimmedMusic`), and checks
each at -30 to -8 LUFS, true peak at or below 0 dBTP, at most 1 s of leading
and 2 s of internal silence, then the change between them, and that chimes at
playback gain (80%, linear) stay at least 3 LU above music at mpv volume 60
(cubic, about -13.3 dB). Missing `ELEVENLABS_API_KEY` skips unless
`VOX_LEVELS_REQUIRE_LIVE=1`, as in nightly CI.

Run `make test-agent` with Claude Code on `PATH` and `ANTHROPIC_API_KEY` set.
The test launches a private `voxd` on a fresh loopback port. Headless Haiku
calls the plugin's `mic:rec` tool, which writes real eSpeak speech to the
daemon's recordings store without playing it. The same CPU Whisper oracle
requires WER ≤ **0.02** and rejects a reference with one word changed. The
session is limited to two turns, 90 seconds, and a $0.05 API budget. Missing
CLI or key skips locally; `VOX_AGENT_REQUIRE=1` makes it fail in CI.

## Dev box vs CI

Every gap below has a concrete reason, not a permanent design choice unless
stated otherwise -- the same discipline biff's testing doc holds.

### On your dev box

Use the commands in the [named-tier table](#testing-pyramid). Run the
default tier during development, `subprocess` when changing scripts or their
process boundaries, and `slow` when changing concurrent logging or other
heavy local operations. Run `make install` then `make test-e2e` to verify
the installed wheel. Run `make test-roundtrip` for the offline speech oracle and
`make test-roundtrip-live` for the credentialed matrix. Run `make test-agent`
to test the Claude Code plugin. The reserved `sdk`
command currently selects no tests. Live audio requires manual listening, not packaging smoke.

### In CI

What runs on every push/PR (`.github/workflows/test.yml`): **the default
tier, `slow`, `subprocess`, and installed-wheel `e2e`, in one pytest-cov
invocation.** `make coverage` builds and installs the wheel first, so packaging
tests do not silently skip for lack of an installed binary. The separate
packaging-e2e job remains as a fast packaging diagnostic.
The separate `roundtrip` job enforces offline speech WER on PRs. The shared
`roundtrip-live.yml` suite runs espeak-ng, cloud providers, playback capture,
signal levels, and the Claude agent on Linux, and Say on macOS. The scheduled caller
(`.github/workflows/nightly.yml`) uses the `LINEAR_API_KEY` Actions secret
to file a `repo:vox` Bug linking the failed run attempt. Manual dispatch runs
do not file issues.

The Linux provider and playback jobs assume the Non-Production `vox-polly-qa`
IAM role via GitHub OIDC to use Polly in `us-east-1`. Provision the role using
[the CloudFormation template](.github/vox-polly-oidc.yml) in account
`579096219769`, with the GitHub OIDC provider and audience `sts.amazonaws.com`.
By default only runs on `main` can assume the role. Before merging, protect
`v*` tags in the [Vox rulesets](https://github.com/punt-labs/vox/settings/rules)
by restricting their creation, updates, and deletion to release maintainers,
then set `EnableReleaseTags=true` so release candidates can use the role.
Use `ProofBranch` only as temporary exact trust for a pre-merge proof; clear
it afterward. No AWS key secrets are passed to the suite. On any other ref
(a PR branch dispatch, for example) the suite skips only the role-backed Polly
steps and tests, with a "Polly skipped" notice on the run, so ElevenLabs and
OpenAI still gate; the Polly credential-rejection test uses bogus keys and
still runs. On `main`, nightly, and `v*` tags a failed role assumption fails
the run. To prove Polly on a `ProofBranch`, dispatch with
`gh workflow run roundtrip-live.yml --ref <branch> -f require-polly=true`.

### Coverage policy

Combined coverage excludes `roundtrip` and `levels` explicitly, so its selection
does not depend on whether Whisper or mpv is installed. The dedicated speech job enforces WER;
the coverage ratchet covers default, slow, subprocess, and packaging tiers.

Coverage is measured, not subject to an absolute percentage floor (punt-kit
`ci.md` section 11). On PRs the test job also measures the merge base with the
target branch in a detached worktree, using that revision's tests/dependencies
and the current coverage harness/configuration. Both total line coverage and
total branch coverage must not decrease, implementing the PL-TT-2 no-regression
policy. The comparison uses exact covered/valid fractions, with no epsilon or
rounding allowance. A missing, empty, or invalid report fails closed.

The base measurement passes the head configuration explicitly with the harness's
`--rcfile` option. The harness uses that file for pytest-cov, subprocess startup,
and report generation; it ignores and removes inherited `COVERAGE_RCFILE` from
the test environment so nested harness calls keep their own configuration.

There is no committed numeric baseline to raise: improvements automatically
become the comparison point after merging. Never lower a floor or regenerate a
baseline to bypass a regression; add tests instead. Locally run `make coverage`
and compare reports with
`make coverage-compare COVERAGE_BASE=/path/to/base.json`. Without `COVERAGE_BASE`,
`make coverage-compare` publishes the current totals without a comparison.

The job summary publishes line and branch percentages. The `coverage` artifact
contains XML, JSON, and browsable HTML; terminal output lists missing lines.
The runner temporarily installs `coverage.process_startup()` `.pth` hooks in
both the test venv and the installed wheel's tool environment, and exports
`COVERAGE_PROCESS_START`. Parallel data is combined, with installed-wheel paths
mapped back to source. Hooks are removed on exit. Processes that deliberately
strip the environment or are killed before coverage can flush cannot contribute
data; shell scripts themselves are not Python coverage. Live audio/hardware and
human-judged tests remain outside automated CI. `sdk` remains empty; the
live provider and agent checks run in the shared nightly workflow.

## Follow-ups

- Evaluate `pytest-xdist` separately, including fixture isolation and
  measured performance under parallel execution.
- Profile the current default tier before proposing runtime fixes; the
  historical contention theory is not a diagnosis of today's suite.

## Quality Gates

Each commit must pass the commit hook's checks. Tests are also one of the
gates in full `make check`, which must pass at handoff or before opening a PR:

```bash
make check
```

All gates must show zero errors. No exceptions for "pre-existing failures."
