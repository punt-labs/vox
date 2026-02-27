# Changelog

All notable changes to punt-tts will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Batch `set_config` mode: pass `updates` dict to write multiple config fields in a single atomic read-write ([#33](https://github.com/punt-labs/tts/pull/33))

### Fixed

- Vibe tags (`[excited]`, `[weary]`, etc.) are now only prepended when the provider supports expressive tags (ElevenLabs). Other providers (Polly, OpenAI, say, espeak) no longer speak bracketed tag text literally ([#39](https://github.com/punt-labs/tts/pull/39))
- Speak hook output now uses gendered pronouns: "matilda said her piece" instead of "matilda said the piece" ([#31](https://github.com/punt-labs/tts/pull/31))
- `install.sh` now uses uninstall+install instead of `claude plugin update` which did not reliably pick up new versions
- `tts doctor` MCP check now says "Claude Desktop MCP" (not "MCP server") and suggests the correct command (`tts install-desktop`)

## [0.7.0] - 2026-02-27

### Added

- macOS `say` command fallback provider: zero-config, offline TTS when no API keys are configured. Uses Fred voice (the iconic 1984 Mac voice) to nudge users toward configuring a real provider
- Linux `espeak-ng` fallback provider: zero-config, offline TTS using espeak-ng speech synthesizer. Auto-detected when espeak-ng is installed and no API keys are set
- Auto-detection now falls back to system TTS on both platforms: `say` on macOS, `espeak` on Linux (when installed). Final fallback remains `polly`
- `--provider say` and `--provider espeak` flags for explicit use of system voices
- Installer and `tts doctor` now check for espeak-ng on Linux and show install hints when no API keys are configured

## [0.6.1] - 2026-02-27

### Fixed

- Stop hook no longer leaks `vibe_mode` debug data in user-visible hook error display; vibe data now read from `.tts/config.md` via Read tool ([#26](https://github.com/punt-labs/tts/pull/26))
- `_apply_vibe()` skips vibe tag prepend when text already starts with an expression tag, preventing `[calm] [calm] ...` doubling ([#27](https://github.com/punt-labs/tts/pull/27))
- Voice name now appears in speak tool panel output; `suppress-output.sh` unwraps the `{"result": "..."}` wrapper Claude Code adds to MCP tool responses
- `install.sh` now detects already-installed plugin and runs `claude plugin update` instead of silently no-oping ([#28](https://github.com/punt-labs/tts/pull/28))
- TestPyPI verification in release CI uses `--refresh` to bust stale uv index cache between retry attempts ([#29](https://github.com/punt-labs/tts/pull/29))

## [0.6.0] - 2026-02-27

### Added

- `/vibe` command with three modes: `auto` (default — detects session mood from signals), `manual` (`/vibe <mood>` — user-specified), and `off`
- Auto-vibe: PostToolUse hook on Bash accumulates session signals (test pass/fail, lint, git ops) and stop-hook continuation passes them to Claude for expressive tag selection
- `set_config` MCP tool: writes plugin config fields atomically, replacing Read/Write/Edit file-tool pattern for all config mutations
- Panel display for vibe shifts: `♪ vibe shifted to [weary]` on config writes
- Shell linting with shellcheck in quality gates and CI

### Changed

- `/vibe`, `/notify`, `/speak`, `/voice` commands now use `set_config` MCP tool instead of Read/Write/Edit file tools
- Panel output personifies the voice: `♪ matilda has spoken` instead of `♪ spoken — matilda (elevenlabs)`. Provider name dropped from display. Phrase pool adds variety.

## [0.5.0] - 2026-02-27

### Fixed

- Installer now refreshes marketplace clone before plugin install, ensuring existing users get the correct `source.ref` pins

## [0.4.0] - 2026-02-27

### Added

- Dev/prod namespace isolation for plugin testing (`claude --plugin-dir .`)
- Audio playback serialized via `flock` — concurrent utterances queue instead of overlapping or being killed
- `tts play <file>` CLI command for flock-serialized audio playback (used by hooks)
- Cross-platform audio player: `afplay` (macOS) with `ffplay` (Linux/cross-platform) fallback

### Changed

- MCP server key renamed from `tts` to `vox`; tools now appear as `plugin:tts:vox` (was `plugin:tts:tts`)
- Installer and session-start hook clean up legacy `mcp__plugin_tts_tts__*` permission entries from pre-rename installs

## [0.3.6] - 2026-02-26

### Changed

- ElevenLabs provider now uses streaming API (`text_to_speech.stream()`) for lower time-to-first-audio

### Fixed

- Installer now runs `claude plugin update` when plugin is already installed; previously users stayed stuck on old versions
- Chime playback detached from hook process group (`nohup + disown`) so audio survives hook exit
- `--scope user` added to `claude plugin update` command (Bugbot catch)

## [0.3.5] - 2026-02-26

### Changed

- Config moved from global `~/.claude/tts.local.md` to per-project `.tts/config.md`; settings no longer leak across projects

### Added

- `argument-hint` frontmatter on `/notify`, `/speak`, `/say`, `/voice` commands for autocomplete-style picker hints

## [0.3.4] - 2026-02-26

### Fixed

- Plugin name is now `tts` on main (was `tts-dev`); marketplace installs show `plugin:tts:tts` instead of `plugin:tts-dev:tts`
- MCP server command is now `tts serve` (was `uv run tts serve`); works on machines without `uv`
- Install script handles SSH auth failure with HTTPS fallback ([#8](https://github.com/punt-labs/tts/issues/8))

### Removed

- Dev/prod plugin name swap scripts (`release-plugin.sh`, `restore-dev-plugin.sh`); plugin.json is always prod-ready

## [0.3.3] - 2026-02-25

### Added

- MCP server `instructions` field primes stop hook behavior at session start (prior-context delivery pattern from biff)

### Changed

- Stop hook phrases now imply the action naturally ("Speaking my thoughts...", "Saying my piece...") — playful for the user, instructive for the model

## [0.3.2] - 2026-02-25

### Changed

- Stop hook reason now shows playful, randomized phrases ("Clearing my throat...", "Finding my words...") instead of functional text

## [0.3.1] - 2026-02-25

### Fixed

- MCP tools now return valid JSON instead of Python repr (fixes panel display showing raw dict)
- Stop hook reason is a clean single line instead of a wall of instructions
- CLI no longer crashes on `tts install` when no TTS provider API keys are configured (lazy provider init)

## [0.3.0] - 2026-02-25

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
