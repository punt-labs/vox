# Changelog

All notable changes to punt-tts will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-02-25

### Added

- Multi-provider TTS engine extracted from langlearn-tts
- ElevenLabs, AWS Polly, and OpenAI TTS providers
- CLI commands: synthesize, batch, pair, pair-batch, doctor, install, serve
- MCP server with synthesize, batch, pair, and pair-batch tools
- Ephemeral output mode (`.tts/` directory in cwd) for transient audio
- Claude Code plugin shell: plugin.json, hooks, /voice and /say commands
- Auto-detection: ElevenLabs > Polly (based on available API keys)
- GitHub Actions CI: lint and test workflows
