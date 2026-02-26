# punt-tts

> Voice for your AI coding assistant.

[![License](https://img.shields.io/github/license/punt-labs/tts)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/punt-labs/tts/test.yml?label=CI)](https://github.com/punt-labs/tts/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/punt-tts)](https://pypi.org/project/punt-tts/)

When Claude Code finishes a task, hits an error, or needs your approval --- you hear it. No need to watch the terminal. Keep working; your agent will tell you what happened.

**Platforms:** macOS

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/tts/main/install.sh | sh
```

Restart Claude Code, then:

```text
/notify y     # hear when tasks complete or need input
/recap        # spoken summary of what just happened
```

<details>
<summary>Manual install (if you already have uv)</summary>

```bash
uv tool install punt-tts
tts install
tts doctor
```

</details>

<details>
<summary>Verify before running</summary>

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/tts/main/install.sh -o install.sh
shasum -a 256 install.sh
cat install.sh
sh install.sh
```

</details>

## Features

- **Notification layer** --- spoken summaries when tasks finish, chimes when Claude needs input
- **Three providers** --- ElevenLabs (natural voice), OpenAI (low latency), AWS Polly (cost-effective)
- **Opt-in only** --- no audio until you enable it, no surprises
- **Voice or chime** --- `/speak n` switches to audio tones, no TTS API calls
- **Graceful absence** --- if punt-tts isn't installed, Claude Code works exactly as before
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
| `/voice on` \| `/voice off` | Enable/disable voice mode |

## Providers

punt-tts auto-detects the best available provider.

| Provider | API Key | Default Voice | Best For |
|----------|---------|---------------|----------|
| ElevenLabs | `ELEVENLABS_API_KEY` | matilda | Long-form summaries, natural voice |
| OpenAI | `OPENAI_API_KEY` | nova | Fast notifications, low latency |
| AWS Polly | AWS credentials | joanna | Cost-effective, no API key needed |

Auto-detection order: ElevenLabs > OpenAI > Polly.

## CLI

punt-tts is also a standalone TTS tool, independent of Claude Code.

```bash
tts synthesize "Hello world"                  # Synthesize with default provider
tts synthesize "Hello" --provider elevenlabs  # Use specific provider
tts doctor                                     # Check setup
tts install                                    # Install Claude Code plugin (marketplace)
tts uninstall                                  # Remove plugin and clean up
tts install-desktop                            # Register MCP server with Claude Desktop
tts serve                                      # Start MCP server (stdio)
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TTS_PROVIDER` | Force a specific provider | auto-detect |
| `TTS_MODEL` | Model override | provider default |
| `TTS_OUTPUT_DIR` | Output directory | `~/tts-output` |

## Roadmap

### Shipped

- Notification layer: `/notify`, `/speak`, `/recap`, Stop + Notification hooks
- Multi-provider TTS engine: ElevenLabs, AWS Polly, OpenAI
- Claude Code plugin: marketplace install, MCP server, slash commands
- CLI: synthesize, batch, pair, pair-batch, doctor
- Ephemeral output mode (`.tts/` in cwd)

### Next

| Phase | What Ships |
|-------|-----------|
| **Screen reader** | macOS VoiceOver and `say` fallback when no API key is configured |
| **Streaming** | Real-time audio streaming for long summaries |
| **Voice selection** | `/voice pick` command with preview and persistence |

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
