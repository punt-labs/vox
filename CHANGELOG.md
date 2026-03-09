# Changelog

All notable changes to punt-vox will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Continuous mode hooks**: UserPromptSubmit acknowledgment, SubagentStart/Stop announcements, SessionEnd farewell speech — all fire only in continuous mode (`/vox c`) except SessionEnd which fires whenever notify != off
- Centralized quip registry (`quips.py`) for all hook speech phrases — localization and theming ready
- Shared `_speak_phrase()` helper eliminates duplication across continuous-mode hook handlers

## [1.3.0] - 2026-03-09

## [1.2.4] - 2026-03-08

### Changed

- Switch default ElevenLabs model from `eleven_v3` to `eleven_flash_v2_5` (~75ms latency, 40k char limit)
- Correct `eleven_v3` per-request character limit to 5,000

### Added

- Mid-session model switching via `/vox model <name>` (shorthands: `v3`, `flash`, `turbo`, `multilingual`)
- Mid-session provider switching via `/vox provider <name>` (`elevenlabs`, `openai`, `polly`, `say`, `espeak`)
- `provider` and `model` fields in `.vox/config.md` session config
- Expressive tags (`[excited]`, `[warm]`, etc.) are now model-aware — only applied on `eleven_v3`

### Fixed

- Config model no longer leaks across incompatible providers (e.g. ElevenLabs model passed to OpenAI)

## [1.2.3] - 2026-03-08

### Fixed

- `vox install` used wrong plugin ID (`tts@punt-labs` instead of `vox@punt-labs`)
- `install.sh` pinned to stale version 1.2.0 instead of current release

## [1.2.2] - 2026-03-08

### Added

- `/mute` replies with a random personality phrase instead of silent confirmation (#83)
- PreCompact hook plays a playful "be right back" message before context compaction in continuous mode (#83)
- Distinct `"compact"` chime signal for PreCompact (distinguishable from task-complete `"done"`)

### Fixed

- Global `--json` flag now placed before subcommand for correct typer parsing in hook subprocess calls
- PreCompact CLI command drains stdin to prevent pipe backpressure
- Z spec partition test coverage for notify/speak state machine (#82)

## [1.2.1] - 2026-03-06

## [1.2.1] - 2026-03-07

### Fixed

- Clean stop hook reason — no internal data leak (#75)
- Align CLI with punt-kit standards (#76)
- Address Copilot review feedback from PR #76 (#77)
- Remove `[skip ci]` from release-plugin.sh (suppressed tag-triggered releases)

### Changed

- Add Makefile per makefile.md standard (#78)

## [1.2.0] - 2026-03-05

### Added

- Per-segment `vibe_tags` in `unmute` and `record` — each segment can now specify its own expressive tags, overriding the session default (same pattern as `voice` and `language`)

### Changed

- `unmute` MCP tool is now non-blocking — synthesis and playback run in a background thread; tool returns predicted metadata immediately
- `record` MCP tool is now non-blocking — returns the predicted file path immediately; synthesis completes in background
- CLI `status` now includes `vibe_tags` and `vibe_signals` in both text and `--json` output (parity with MCP `status` tool)

## [1.1.1] - 2026-03-05

### Fixed

- suppress-output hook now formats `notify`, `speak`, and `status` MCP tool output as compact panel lines instead of dumping raw JSON

## [1.1.0] - 2026-03-05

### Added

- `notify` MCP tool: set notification mode (y/n/c) and session voice without Bash
- `speak` MCP tool: toggle spoken notifications (y/n) without Bash
- `status` MCP tool: query current vox state (provider, voice, notify, vibe) without Bash

### Changed

- All slash commands (`/vox`, `/unmute`, `/mute`, `/vibe`) now use MCP tools exclusively — no Bash, Read, or Write tool permissions needed (DES-017)
- Stop hook block reason now embeds vibe context (mode, mood, signals) so the model never needs to Read `.vox/config.md`

## [1.0.3] - 2026-03-05

### Fixed

- SessionStart hook now updates stale commands on plugin upgrade — previously deployed commands in `~/.claude/commands/` were never refreshed, leaving users with old allowed-tools, old MCP tool names, and prompt-driven logic instead of CLI calls

## [1.0.2] - 2026-03-05

### Fixed

- Provider auto-detection now prefers AWS Polly over macOS `say` and Linux `espeak` when AWS credentials are valid — Polly produces significantly better audio than local fallbacks
- Session voice gracefully falls back to provider default when the configured voice isn't available in the current provider (e.g. ElevenLabs voice "sarah" stored in config but Polly is the active provider)
- Suppressed elevenlabs SDK pydantic v1 `UserWarning` on Python 3.14+ — their upstream issue, filtered at runtime and in test config

## [1.0.1] - 2026-03-05

### Changed

- CLI: replaced `vox on`/`vox off`/`vox mute` with `vox notify y|n|c`, `vox speak y|n`, and `vox voice <name>` — aligns CLI with the standard that every slash command has a corresponding CLI command
- Slash commands `/vox`, `/unmute`, `/mute` now delegate to CLI via Bash instead of manually editing `.vox/config.md` with Read/Edit tools
- Slash commands no longer request `Edit`, `Read`, or `Write` tool permissions — only `Bash` and MCP tools

## [1.0.0] - 2026-03-05

### Fixed

- `/unmute` now sets `notify: "c"` (continuous mode) so spoken notifications actually fire — previously only set `speak: "y"` while `notify` defaulted to `"n"`, silently disabling all notifications
- `/mute` now specifies exact config file location and create-if-missing behavior, matching other config-writing commands
- Hooks resolve `.vox/config.md` via `git rev-parse --git-common-dir` so config is found from worktrees
- All config-writing commands now specify exact `.vox/config.md` location and create-if-missing behavior, preventing agents from searching other directories
- Config path resolution now catches `subprocess.TimeoutExpired` (was `TimeoutError`, which subprocess.run never raises)
- Hook chime resolution uses `CLAUDE_PLUGIN_ROOT` env var for asset paths, fixing chime playback for pip-installed packages where `__file__` resolves into site-packages
- `notify-permission.sh` called non-existent `vox synthesize` — now uses `vox unmute` via the Python hook dispatcher
- Signal classifier now checks lint patterns before test patterns, fixing false matches where "Found N errors" was classified as `tests-fail` instead of `lint-fail`
- Signal classifier uses `re.MULTILINE` so `^` anchors match line starts in multi-line bash output (matches original bash `grep` behavior)
- Chime filename resolution normalizes signal hyphens to underscores (`tests-pass` → `chime_tests_pass.mp3`)
- Checkmark pattern in signal classifier uses literal `✓` instead of raw `\u2713` which was never interpreted as Unicode

### Changed

- Merged `/vox-on` and `/vox-off` into a single `/vox` slash command with `y` (enable), `n` (disable), or `c` (continuous) argument
- `/vox y` and `/vox c` now preserve existing `speak` setting on subsequent calls; only first init defaults `speak` to `"y"`
- Migrated hook business logic from bash to Python via `vox hook <event>` CLI dispatcher — stop, post-bash, and notification hooks are now thin shell gates delegating to testable pure functions in `hooks.py`
- Deleted `hooks/state.sh` — all config reading, mood classification, chime resolution, and audio helpers now use their Python equivalents

## [0.11.0] - 2026-03-04

### Added

- **Mic API**: new MCP server key `mic` with four tools: `unmute` (synthesize + play), `record` (synthesize + save), `vibe` (session mood), `who` (voice roster)
- Both `unmute` and `record` accept a uniform `segments` list — callers no longer need different tools for different cardinalities
- CLI product commands: `vox unmute`, `vox record`, `vox vibe`, `vox on`/`off`, `vox mute`, `vox version`, `vox status`
- Slash commands: `/unmute [@voice]`, `/mute`, `/vox on`, `/vox off`
- Shared modules: `resolve.py` (voice/language/output resolution, vibe application), `voices.py` (blurbs, excuses), `config.py` write API
- Vibe-driven chime notifications: chimes now reflect session mood (bright/neutral/dark) via pitch-shifted variants (±3 semitones)
- Mood classification module (`mood.py`): maps free-form vibe strings to bright/neutral/dark tonal families
- Mood-aware chime resolution: `resolve_chime_path(signal, mood=)` with four-level fallback chain
- Per-signal chime assets: distinct sounds for tests-pass, tests-fail, lint-pass, lint-fail, git-push-ok, and merge-conflict (chime mode only)
- Signal-aware `resolve_chime_path(signal)` with automatic fallback to `chime_done.mp3`
- Generation script `scripts/generate_chimes.py` for reproducible chime synthesis with mood variants

### Changed

- MCP server key from `vox` to `mic` (`plugin:vox:mic`)
- Hook matchers updated from `_vox__` to `_mic__` patterns
- Session-start hook auto-migrates old permission patterns and cleans retired commands

### Removed

- MCP tools: `speak`, `chorus`, `duet`, `ensemble`, `set_config`, `list_voices`
- CLI commands: `synthesize`, `synthesize-batch`, `synthesize-pair`, `synthesize-pair-batch`
- Slash commands: `/say`, `/speak`, `/notify`, `/voice`

## [0.10.1] - 2026-03-03

### Fixed

- Plugin name on release tags is now `vox` (was `vox-dev` — release script was not run before v0.10.0 tag)

## [0.10.0] - 2026-03-03

First release as **punt-vox**. The PyPI package name changed from `punt-tts` to `punt-vox`, the CLI binary changed from `tts` to `vox`, and all internal paths and namespaces follow suit. No functional changes — this is a pure rename release.

### Changed

- Rename env var `TTS_OUTPUT_DIR` → `VOX_OUTPUT_DIR`
- Rename default output dir `~/tts-output` → `~/vox-output`
- Rename ephemeral dir `.tts/` → `.vox/` (config, audio)
- Rename log/state dir `~/.punt-tts/` → `~/.punt-vox/` (logs, playback lock, pending queue)
- Release workflow installs `punt-vox` and verifies `vox --help` (was `punt-tts`/`tts`)
- `install.sh` installs `punt-vox` package with `vox` binary (was `punt-tts`/`tts`)
- Rename plugin name `tts-dev`/`tts` → `vox-dev`/`vox` (plugin.json, hooks, commands, settings)
- Plugin MCP tool namespace `mcp__plugin_tts_vox__*` → `mcp__plugin_vox_vox__*`
- Session-start hook cleans up all legacy `mcp__plugin_tts*` permission patterns
- Hook scripts use `vox` CLI binary (was `tts`)
- All documentation updated: README.md, CLAUDE.md, DESIGN.md, prfaq.tex — `punt-tts`/`tts` → `punt-vox`/`vox`

### Fixed

- `release-plugin.sh` no longer fails when no `-dev` commands exist — name swap proceeds with a warning instead of aborting
- `restore-dev-plugin.sh` no longer fails when `.claude/commands/` directory doesn't exist at the release commit
- PostToolUse hook matcher now fires for standalone MCP server registrations (`mcp__vox__*`) in addition to plugin-namespaced names

## [0.9.0] - 2026-02-28

### Added

- `list_voices` MCP tool: browse available voices for the current provider with curated personality blurbs, shuffled featured list (capped at 6), and full voice roster
- `/voice` bare invocation: displays featured voices with blurbs and prompts user to pick with `/voice <name>`
- `list_voices` panel handler in suppress-output hook: displays voice count or "here's who's around"

### Fixed

- Permission notification hook now uses the active session voice instead of always defaulting to matilda

## [0.8.1] - 2026-02-28

### Fixed

- eSpeak provider now passes language codes (e.g. `en-us`) to `espeak-ng -v` instead of display names (e.g. `English_(America)`) which espeak-ng rejects
- Installer uses `doctor || true` so diagnostic failures don't abort the install script under `set -eu`

## [0.8.0] - 2026-02-28

### Added

- Per-session voice selection: `/voice <name>` sets a default voice for all speak/chorus/duet/ensemble calls. Stored in `.tts/config.md` as `voice` field. Use `/voice clear` to revert to provider default.
- Session event watcher: daemon thread in MCP server tails the session transcript and announces milestones (tests passed, lint clean, code pushed) in real-time when `notify=c`. Uses pattern matching on bash tool output, per-signal throttle, and voice/chime modes.
- `vibe_tags` parameter on `speak` and `chorus` tools: applies expressive tags and clears `vibe_signals` in one step, replacing the separate `set_config` call in the stop-hook flow.
- Friendly voice-not-found errors: when a voice can't be resolved, providers raise `VoiceNotFoundError` with structured data. MCP tool handlers catch it and return a playful message (e.g. "bob stepped out for a coffee. How about matilda, aria, charlie?") instead of a raw traceback.

### Fixed

- Audio no longer clips at the end: 150ms trailing silence appended to all output files (single, stitched, and batch) to prevent MP3 frame truncation.

### Changed

- macOS Say provider default voice changed from Fred to Samantha.
- Stop hook now signal-gated: only fires when `vibe_signals` is non-empty (real work happened). Prevents empty recaps after trivial commands like `/say hello`.
- Signal accumulation decoupled from `vibe_mode`: signals accumulate in all modes (auto, manual, off), not just auto. Required for stop hook gating.
- New signal types: `git-commit` (commit created) and `pr-created` (pull request opened).

## [0.7.1] - 2026-02-27

### Added

- Batch `set_config` mode: pass `updates` dict to write multiple config fields in a single atomic read-write ([#33](https://github.com/punt-labs/vox/pull/33))

### Fixed

- Vibe tags (`[excited]`, `[weary]`, etc.) are now only prepended when the provider supports expressive tags (ElevenLabs). Other providers (Polly, OpenAI, say, espeak) no longer speak bracketed tag text literally ([#39](https://github.com/punt-labs/vox/pull/39))
- Speak hook output now uses gendered pronouns: "matilda said her piece" instead of "matilda said the piece" ([#31](https://github.com/punt-labs/vox/pull/31))
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

- Stop hook no longer leaks `vibe_mode` debug data in user-visible hook error display; vibe data now read from `.tts/config.md` via Read tool ([#26](https://github.com/punt-labs/vox/pull/26))
- `_apply_vibe()` skips vibe tag prepend when text already starts with an expression tag, preventing `[calm] [calm] ...` doubling ([#27](https://github.com/punt-labs/vox/pull/27))
- Voice name now appears in speak tool panel output; `suppress-output.sh` unwraps the `{"result": "..."}` wrapper Claude Code adds to MCP tool responses
- `install.sh` now detects already-installed plugin and runs `claude plugin update` instead of silently no-oping ([#28](https://github.com/punt-labs/vox/pull/28))
- TestPyPI verification in release CI uses `--refresh` to bust stale uv index cache between retry attempts ([#29](https://github.com/punt-labs/vox/pull/29))

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
- Install script handles SSH auth failure with HTTPS fallback ([#8](https://github.com/punt-labs/vox/issues/8))

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
