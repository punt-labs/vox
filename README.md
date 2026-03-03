# punt-vox

> Voice for your AI coding assistant.

[![License](https://img.shields.io/github/license/punt-labs/tts)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/punt-labs/tts/test.yml?label=CI)](https://github.com/punt-labs/tts/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/punt-vox)](https://pypi.org/project/punt-vox/)
[![Python](https://img.shields.io/pypi/pyversions/punt-vox)](https://pypi.org/project/punt-vox/)
[![Working Backwards](https://img.shields.io/badge/Working_Backwards-hypothesis-lightgrey)](./prfaq.pdf)

When Claude Code finishes a task, hits an error, or needs your approval --- you hear it. No need to watch the terminal. Keep working; your assistant will tell you what happened.

**Platforms:** macOS, Linux

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/tts/2d8922f/install.sh | sh
```

Restart Claude Code, then:

```text
/notify y     # hear when tasks complete or need input
/recap        # spoken summary of what just happened
```

<details>
<summary>Manual install (if you already have uv)</summary>

```bash
uv tool install punt-vox
vox install
vox doctor
```

</details>

<details>
<summary>Verify before running</summary>

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/tts/2d8922f/install.sh -o install.sh
shasum -a 256 install.sh
cat install.sh
sh install.sh
```

</details>

## Features

- **Notification layer** --- spoken summaries when tasks finish, chimes when Claude needs input
- **Session vibe** --- `/vibe` sets the mood for all speech. Auto-mode reads session signals (test results, lint, git ops) and adapts the voice. Manual mode lets you set it yourself. ElevenLabs expressive tags (`[weary]`, `[excited]`, `[sighs]`) color every utterance.
- **Five providers** --- ElevenLabs, OpenAI, AWS Polly, macOS `say`, and Linux `espeak-ng`. The full experience (natural voice, expressive tags, `/vibe`) requires ElevenLabs.
- **Opt-in only** --- no audio until you enable it, no surprises
- **Voice or chime** --- `/speak n` switches to audio tones, no TTS API calls
- **Graceful absence** --- if punt-vox isn't installed, Claude Code works exactly as before
- **MCP-native** --- runs as a Claude Code plugin with slash commands and hooks

## What It Looks Like

### Enable notifications

```text
> /notify y

Notifications: enabled (voice)
You will hear spoken summaries when tasks complete and chimes when Claude needs input.
```

### Get a recap

```text
> /recap

Speaking: "I refactored the authentication module into three files, added
comprehensive tests for the token refresh flow, and fixed a race condition
in the session middleware. All 47 tests pass."
```

### Set the vibe

```text
> /vibe banging my head against the wall

Vibe: banging my head against the wall → [frustrated] [sighs] [manual]
```

Auto-mode (default) reads session signals and adapts automatically --- after a string of test failures the voice sounds `[weary]`, after a successful release it sounds `[excited]`.

### Switch to chime-only

```text
> /speak n

Speak: off (chime only)
Notifications will use audio tones instead of voice.
```

## Commands

| Command | Purpose |
|---------|---------|
| `/notify y` | Speak on task completion and permission prompts |
| `/notify c` | Continuous --- also speak milestone updates during long tasks |
| `/notify n` | Off |
| `/speak y` | Notifications are spoken (default when /notify is on) |
| `/speak n` | Notifications are a chime --- no words |
| `/recap` | Spoken summary of Claude's last response |
| `/say "text"` | Speak arbitrary text aloud |
| `/vibe <mood>` | Set session mood --- voice adapts to match |
| `/vibe auto` | Auto-detect mood from session signals (default) |
| `/vibe off` | Disable vibe --- neutral voice |
| `/voice on` \| `/voice off` | Enable/disable voice mode |

## Providers

The full experience --- natural voice with expressive tags that respond to `/vibe` --- requires ElevenLabs. The other providers are fallbacks for environments where ElevenLabs isn't available.

| Provider | API Key | Default Voice | Best For |
|----------|---------|---------------|----------|
| **ElevenLabs** | `ELEVENLABS_API_KEY` | matilda | **Recommended.** Natural voice, expressive tags via `/vibe` |
| OpenAI | `OPENAI_API_KEY` | nova | Fallback. Fast notifications, low latency |
| AWS Polly | AWS credentials | joanna | Fallback. Cost-effective, no API key needed |
| macOS say | — | fred | Fallback. Zero-config on macOS, offline |
| espeak-ng | — | en | Fallback. Zero-config on Linux, offline |

Auto-detection order: ElevenLabs > OpenAI > say (macOS) / espeak (Linux) > Polly.

## CLI

punt-vox is also a standalone TTS tool, independent of Claude Code.

```bash
vox synthesize "Hello world"                  # Synthesize with default provider
vox synthesize "Hello" --provider elevenlabs  # Use specific provider
vox doctor                                     # Check setup
vox install                                    # Install Claude Code plugin (marketplace)
vox uninstall                                  # Remove plugin and clean up
vox install-desktop                            # Register MCP server with Claude Desktop
vox serve                                      # Start MCP server (stdio)
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TTS_PROVIDER` | Force a specific provider | auto-detect |
| `TTS_MODEL` | Model override | provider default |
| `VOX_OUTPUT_DIR` | Output directory | `~/vox-output` |

## Roadmap

### Shipped

- Notification layer: `/notify`, `/speak`, `/recap`, Stop + Notification hooks
- Multi-provider TTS engine: ElevenLabs, AWS Polly, OpenAI
- Claude Code plugin: marketplace install, MCP server, slash commands
- CLI: synthesize, batch, pair, pair-batch, doctor
- Ephemeral output mode (`.vox/` in cwd)
- Two-channel display: `♪` panel summaries with voice/provider context
- Playful stop hook phrases: randomized vocalization-themed messages ("Speaking my thoughts...", "Saying my piece...")
- Natural notification phrasing: randomized phrases for permission and idle prompts
- Audio playback serialization via `flock` --- concurrent utterances queue instead of overlapping
- ElevenLabs streaming API for lower time-to-first-audio
- Dev/prod namespace isolation for plugin testing (`claude --plugin-dir .`)
- `/vibe` with auto, manual, and off modes --- ElevenLabs expressive tags color every utterance
- Auto-vibe signal accumulator: test pass/fail, lint, git ops feed mood detection
- `set_config` MCP tool for atomic config mutations (replaces file-tool pattern)
- System fallback providers: macOS `say` and Linux `espeak-ng` for zero-config offline speech

### Coming Soon

| Feature | What It Does |
|---------|-------------|
| **Per-session voices** | Each Claude Code session gets its own voice from a pool --- no more five matildas talking at once. `/voice` to audition and pick. |

## Documentation

[Design Log](DESIGN.md) |
[Changelog](CHANGELOG.md)

## Development

```bash
uv sync --all-extras                           # Install dependencies
uv run ruff check src/ tests/                  # Lint
uv run ruff format --check src/ tests/         # Format
uv run mypy src/ tests/                        # Type check (mypy)
uv run pyright src/ tests/                     # Type check (pyright)
uv run pytest tests/ -v                        # Test
```

## License

MIT
