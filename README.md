# punt-tts

Text-to-speech CLI, MCP server, and Claude Code plugin.

Supports ElevenLabs (premium), AWS Polly, and OpenAI TTS providers.

## Install

```bash
uv tool install punt-tts
```

## Quick Start

```bash
tts doctor                                    # Check setup
tts synthesize "Hello world"                 # Synthesize with default provider
tts synthesize "Hello" --provider elevenlabs  # Use specific provider
tts install                                   # Register MCP server
```

## Providers

| Provider | API Key Env Var | Default Voice |
|----------|----------------|---------------|
| ElevenLabs | `ELEVENLABS_API_KEY` | matilda |
| AWS Polly | AWS credentials | joanna |
| OpenAI | `OPENAI_API_KEY` | nova |

Auto-detection: ElevenLabs (when `ELEVENLABS_API_KEY` set) > Polly (default).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TTS_PROVIDER` | TTS provider | auto-detect |
| `TTS_MODEL` | Model override | provider default |
| `TTS_OUTPUT_DIR` | Output directory | `~/tts-output` |

## License

MIT
