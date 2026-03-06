# punt-vox

> Voice for your AI coding assistant.

[![License](https://img.shields.io/github/license/punt-labs/vox)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/punt-labs/vox/test.yml?label=CI)](https://github.com/punt-labs/vox/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/punt-vox)](https://pypi.org/project/punt-vox/)
[![Python](https://img.shields.io/pypi/pyversions/punt-vox)](https://pypi.org/project/punt-vox/)
[![Working Backwards](https://img.shields.io/badge/Working_Backwards-hypothesis-lightgrey)](./prfaq.pdf)

When Claude Code finishes a task, hits an error, or needs your approval --- you hear it. No need to watch the terminal. Keep working; your assistant will tell you what happened.

**Platforms:** macOS, Linux

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/vox/b99c750/install.sh | sh
```

Restart Claude Code, then:

```text
/vox y        # hear when tasks complete or need input
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
curl -fsSL https://raw.githubusercontent.com/punt-labs/vox/b99c750/install.sh -o install.sh
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
- **Voice or chime** --- `/mute` switches to audio tones, no TTS API calls
- **Graceful absence** --- if punt-vox isn't installed, Claude Code works exactly as before
- **MCP-native** --- runs as a Claude Code plugin with slash commands and hooks

## What It Looks Like

### Enable notifications

```text
> /vox y

Vox enabled. You'll hear when tasks finish or need approval.
Pick a voice with /unmute @<name>.
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
> /mute

Muted — chimes only.
```

Chimes are mood-aware: when a vibe is active, chimes pitch-shift to match (bright for happy sessions, dark for frustrated ones). Eight distinct signals (tests pass/fail, lint pass/fail, git push, merge conflict, done, prompt) × three mood variants = 24 chime assets.

## Commands

| Command | Purpose |
|---------|---------|
| `/vox y` | Enable vox (chime notifications) |
| `/vox n` | Disable vox |
| `/vox c` | Continuous mode (spoken summaries on task completion) |
| `/unmute` | Enable voice mode (spoken notifications) |
| `/unmute @matilda` | Set session voice + enable voice |
| `/unmute @` | Browse voice roster |
| `/mute` | Chimes only --- no voice |
| `/recap` | Spoken summary of Claude's last response |
| `/vibe <mood>` | Set session mood --- voice adapts to match |
| `/vibe auto` | Auto-detect mood from session signals (default) |
| `/vibe off` | Disable vibe --- neutral voice |

## Providers

The full experience --- natural voice with expressive tags that respond to `/vibe` --- requires ElevenLabs. The other providers are fallbacks for environments where ElevenLabs isn't available.

| Provider | API Key | Default Voice | Best For |
|----------|---------|---------------|----------|
| **ElevenLabs** | `ELEVENLABS_API_KEY` | matilda | **Recommended.** Natural voice, expressive tags via `/vibe` |
| OpenAI | `OPENAI_API_KEY` | nova | Fast notifications, low latency |
| AWS Polly | AWS credentials | joanna | Natural voice, cost-effective |
| macOS say | — | samantha | Zero-config on macOS, offline |
| espeak-ng | — | en | Zero-config on Linux, offline |

Auto-detection order: ElevenLabs > OpenAI > Polly (if AWS credentials valid) > say (macOS) / espeak (Linux).

## CLI

punt-vox is also a standalone TTS tool, independent of Claude Code.

```bash
vox unmute "Hello world"                       # Synthesize + play
vox record "Hello world" -o hello.mp3          # Synthesize + save
vox record --from segments.json                # From JSON segments file
vox vibe excited                               # Set session mood
vox notify y                                   # Enable notifications
vox notify c                                   # Continuous spoken mode
vox speak n                                    # Chimes only
vox voice matilda                              # Set session voice
vox status                                     # Current state
vox version                                    # Print version
vox doctor                                     # Check setup
vox install                                    # Install Claude Code plugin
vox mcp                                        # Start MCP server (stdio)
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TTS_PROVIDER` | Force a specific provider | auto-detect |
| `TTS_MODEL` | Model override | provider default |
| `VOX_OUTPUT_DIR` | Output directory | `~/vox-output` |

## Roadmap

### Shipped

- **Mic API**: unified `unmute`/`record`/`vibe`/`who` MCP tools with segment-based input
- Notification layer: `/vox y|n|c`, `/mute`, `/unmute`, `/recap`, Stop + Notification hooks
- Multi-provider TTS engine: ElevenLabs, AWS Polly, OpenAI, macOS `say`, Linux `espeak-ng`
- Claude Code plugin: marketplace install, MCP server, slash commands
- CLI: unmute, record, vibe, on/off, mute, version, status, doctor
- Ephemeral output mode (`.vox/` in cwd)
- Two-channel display: `♪` panel summaries with voice/provider context
- Audio playback serialization via `flock` --- concurrent utterances queue instead of overlapping
- ElevenLabs streaming API for lower time-to-first-audio
- `/vibe` with auto, manual, and off modes --- ElevenLabs expressive tags color every utterance
- Auto-vibe signal accumulator: test pass/fail, lint, git ops feed mood detection
- Per-signal chime assets and vibe-driven chimes with mood-aware pitch shifting

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
