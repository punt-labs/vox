# punt-tts

Voice for your AI coding assistant.

When Claude Code finishes a task, hits an error, or needs your approval — you hear it. No need to watch the terminal. Keep working; your agent will tell you what happened.

## Why

AI coding agents run for minutes at a time. You switch to a browser, a doc, another file. When the agent finishes — or gets stuck waiting for permission — you don't know until you check. Time burns silently.

punt-tts adds a voice layer to Claude Code. Notifications when tasks complete. Spoken summaries on demand. Audio that works while your eyes are somewhere else.

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
<summary>Verify before running</summary>

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/tts/main/install.sh -o install.sh
shasum -a 256 install.sh
cat install.sh
sh install.sh
```

</details>

<details>
<summary>Manual installation</summary>

```bash
uv tool install punt-tts
tts install
tts doctor
```

</details>

## Commands

### /notify — Task notifications

Control when punt-tts speaks. Audio fires on two events: **task done** and **needs input** (permission prompts, questions).

| Command | Behavior |
|---------|----------|
| `/notify y` | Speak on task completion and permission prompts |
| `/notify c` | Continuous — also speak milestone updates during long tasks |
| `/notify n` | Off |

Default when enabled: spoken summaries. Pair with `/speak n` for chime-only.

### /speak — Words or chimes

Toggle whether notifications use spoken language or just an audio tone.

| Command | Behavior |
|---------|----------|
| `/speak y` | Notifications are spoken (default) |
| `/speak n` | Notifications are a chime — no words |

### /recap — Voice summary on demand

After Claude produces a wall of text — a multi-file refactor, test results, an architecture analysis — type `/recap`. Claude extracts the key points and speaks a 30-second summary while you scan the diff.

`/recap` is explicit and one-shot. The agent never speaks autonomously unless `/notify` is enabled.

## Design Principles

- **Voice supplements text, never replaces it.** Every word spoken is also in the terminal.
- **Opt-in only.** No audio until you enable it. No surprises.
- **Zero configuration.** If `tts doctor` passes, everything works. No audio device setup, no voice selection wizard.
- **Graceful absence.** If punt-tts isn't installed, Claude Code works exactly as before.

## Providers

punt-tts supports three TTS backends. It auto-detects the best available provider.

| Provider | API Key | Default Voice | Best For |
|----------|---------|---------------|----------|
| ElevenLabs | `ELEVENLABS_API_KEY` | matilda | Long-form summaries, natural voice |
| OpenAI | `OPENAI_API_KEY` | nova | Fast notifications, low latency |
| AWS Polly | AWS credentials | joanna | Cost-effective, reliable |

Auto-detection order: ElevenLabs > OpenAI > Polly.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TTS_PROVIDER` | Force a specific provider | auto-detect |
| `TTS_MODEL` | Model override | provider default |
| `TTS_OUTPUT_DIR` | Output directory | `~/tts-output` |

## CLI

punt-tts is also a standalone TTS tool, independent of Claude Code.

```bash
tts synthesize "Hello world"                  # Synthesize with default provider
tts synthesize "Hello" --provider elevenlabs  # Use specific provider
tts doctor                                     # Check setup
tts install                                    # Register MCP server with Claude Code
tts serve                                      # Start MCP server (stdio)
```

## Library

punt-tts is a Python library first. Use it programmatically:

```python
from punt_tts import TTSClient

client = TTSClient()
result = client.synthesize("Hello world", output_path)
```

## License

MIT
