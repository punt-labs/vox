# Testing

519 tests, no network or API dependencies. Every test runs fully offline — no API keys, no network, no audio hardware — but they do rely on local system binaries (ffmpeg for MP3 encoding, optionally espeak-ng).

## Philosophy

Vox has five TTS providers (ElevenLabs, OpenAI, Polly, macOS Say, espeak-ng), a plugin hook system, an MCP server, and a CLI. All of these talk to external services. The test suite proves correctness without ever calling them.

**Core principle**: mock at the provider boundary, test everything above it as real code.

## Architecture

```
tests/
  conftest.py               # Shared fixtures: mock clients, voice caches, valid MP3 bytes
  test_types.py              # Domain types: SynthesisRequest, SynthesisResult, MergeStrategy
  test_core.py               # TTSClient orchestration: batching, SSML, stitching, merge
  test_output.py             # Output path resolution
  test_ephemeral.py          # Ephemeral .vox/ directory lifecycle
  test_playback.py           # flock-serialized audio playback
  test_config.py             # .vox/config.md YAML frontmatter read/write
  test_mood.py               # Mood classification (bright/dark/neutral)
  test_hooks.py              # Claude Code hook dispatchers: stop, post-bash, notification
  test_cli.py                # Typer CLI invocations via CliRunner
  test_server.py             # MCP server tools: unmute, record, vibe, notify, status
  test_watcher.py            # File watcher for config hot-reload
  test_polly_provider.py     # AWS Polly provider
  test_openai_provider.py    # OpenAI TTS provider
  test_elevenlabs_provider.py # ElevenLabs provider
  test_say_provider.py       # macOS Say provider
  test_espeak_provider.py    # espeak-ng provider
```

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

Every provider has a module-level voice cache (`VOICES` dict + `_voices_loaded` flag) that's populated on first use by calling the provider API. Four `autouse=True` fixtures in `conftest.py` pre-populate these caches before every test and restore them after:

- `_populate_voice_cache` — Polly voices (Joanna, Hans, Tatyana, Seoyeon)
- `_populate_elevenlabs_voice_cache` — ElevenLabs voices (Matilda, Drew)
- `_populate_say_voice_cache` — macOS Say voices (Fred, Samantha, Anna)
- `_populate_espeak_voice_cache` — espeak voices (English, German, French)

Tests that verify the cache-loading behavior itself (e.g., "does `resolve_voice` call the API when the cache is empty?") explicitly clear `VOICES` and reset `_voices_loaded` before their test logic.

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

Hook tests (`test_hooks.py`) verify the Claude Code plugin integration — stop hooks, post-bash signal accumulation, and notification dispatch. These mock at two boundaries:

1. **Config I/O** — `write_field`, `write_fields`, `resolve_config_path` are patched to avoid filesystem interaction
2. **Audio dispatch** — `_enqueue_audio` and `subprocess.run` are patched to prevent actual playback

The `_make_config()` helper constructs `VoxConfig` objects directly, bypassing file parsing. This isolates hook logic from config parsing logic (which has its own tests in `test_config.py`).

Key patterns tested:
- Stop hook returns only a `♪` phrase with no internal data
- Auto-vibe mode writes resolved tags to config and clears consumed signals
- Manual/off vibe modes skip config writes
- Signal classification maps bash output to signal tokens (tests-pass, lint-fail, etc.)
- Signal accumulation appends timestamped tokens across multiple bash invocations

## Server Testing

Server tests (`test_server.py`) exercise MCP tool functions directly — `unmute()`, `record()`, `vibe()`, `notify()`, `status()`, `who()`. A `_patch_config` fixture creates a temp config file and monkeypatches the module-level config path, so tools read/write real YAML frontmatter in an isolated temp directory.

Provider construction is patched (`get_provider`, `TTSClient`) so no API clients are created. The tests verify argument threading (voice, language, rate, vibe tags), config side effects, and error messages.

## CLI Testing

CLI tests (`test_cli.py`) use Typer's `CliRunner` to invoke commands as subprocesses would. Provider construction and audio playback are patched. Tests verify exit codes, stdout/stderr output, and argument parsing.

## Config Testing

Config tests (`test_config.py`) use `tmp_path` fixtures to create real `.vox/config.md` files with YAML frontmatter. Tests verify:
- Field reading with and without quotes
- Field writing (insert, update, multi-field atomic writes)
- Key validation (unknown keys raise `ValueError`)
- Edge cases: missing file, empty frontmatter, no frontmatter delimiters

## Watcher Testing

Watcher tests (`test_watcher.py`) exercise the file-watching thread that hot-reloads config changes. Tests use real temp files and short poll intervals. Platform-specific tests (macOS `fsevents`, Linux `select`) are skipped on unsupported platforms via `pytest.mark.skipif`.

## Running Tests

```bash
# Full suite (required before every commit)
uv run pytest tests/ -v

# Single file
uv run pytest tests/test_hooks.py -v

# Single test
uv run pytest tests/test_hooks.py::TestHandleStop::test_voice_mode_blocks_clean_reason -v

# With coverage
uv run pytest tests/ --cov=src --cov-report=term-missing
```

## Integration Tests

Tests requiring real API credentials are marked `@pytest.mark.integration` and excluded from the default run. None currently exist — all provider tests use mocks. The marker is reserved for future smoke tests against live APIs.

## Quality Gates

Tests are one of six gates that must pass before every commit:

```bash
uv run ruff check src/ tests/        # Lint
uv run ruff format --check src/ tests/ # Format
uv run mypy src/ tests/               # Type check (mypy strict)
uv run pyright src/ tests/            # Type check (pyright strict)
uv run pytest tests/ -v               # Tests
shellcheck -x hooks/*.sh scripts/*.sh install.sh  # Shell lint
```

All six must show zero errors. No exceptions for "pre-existing failures."
