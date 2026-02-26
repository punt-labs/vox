# Changelog

All notable changes to punt-tts will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Renamed MCP tools: `synthesize` -> `speak`, `synthesize_batch` -> `chorus`, `synthesize_pair` -> `duet`, `synthesize_pair_batch` -> `ensemble`
- Tool panel output now shows voice and provider context with `♪` prefix (two-channel display pattern)
- Permission and idle notification phrases now vary randomly instead of repeating the same line

## [0.2.0] - 2026-02-25

### Added

- `/notify` command: toggle task-completion and permission-prompt notifications (y/c/n)
- `/speak` command: toggle voice vs chime-only notifications (y/n)
- `/recap` command: on-demand spoken summary of Claude's last response
- Stop hook: blocks Claude's stop to generate a spoken summary when /notify is enabled
- Notification hook: async audio alerts for permission prompts and idle prompts
- Chime audio: bundled MP3 tones for `/speak n` mode (task-complete and needs-approval)
- Shared hook state library (`hooks/state.sh`) for reading tts.local.md from bash
- `tts install`: marketplace-based installation via `claude plugin install tts@punt-labs`
- `tts uninstall`: full cleanup (plugin, commands, permissions, marketplace)
- Marketplace installer module (`installer.py`) for punt-labs marketplace registration
- Release scripts: `scripts/release-plugin.sh` and `scripts/restore-dev-plugin.sh`
- DESIGN.md: design decision log for notification architecture

### Changed

- `tts install` now installs via the Claude Code marketplace (previously wrote Claude Desktop config)
- Old Claude Desktop install behavior moved to `tts install-desktop`
- Plugin uses `uv run` in dev mode for working tree source exercise

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
