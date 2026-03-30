# punt-vox Design Decision Log

This file is the authoritative record of design decisions, prior approaches, and their outcomes. **Every design change must be logged here before implementation.**

## Rules

1. Before proposing ANY design change, consult this log for prior decisions on the same topic.
2. Do not revisit a settled decision without new evidence.
3. Log the decision, alternatives considered, and outcome.

---

## System Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                        Claude Code UI                        │
│                                                              │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  Tool Result  │  │ Assistant Output │  │  Slash Cmds   │  │
│  │    Panel      │  │   (LLM emits)    │  │  /notify etc  │  │
│  └──────┬───────┘  └────────▲─────────┘  └───────┬───────┘  │
│         │                   │                     │          │
└─────────┼───────────────────┼─────────────────────┼──────────┘
          │ updatedMCP        │ model output         │ skill
          │ ToolOutput        │                     │ prompt
          │                   │                     │
┌─────────┴───────────────────┴─────────────────────┴──────────┐
│                        Hook Layer                             │
│                                                              │
│  Stop hook (notify.sh):                                      │
│    if notify=y and not stop_hook_active:                     │
│      decision=block, reason="summarize + call TTS"           │
│    → Claude generates 1-2 sentence summary + calls speak     │
│    → stop_hook_active=true on second fire → let stop         │
│                                                              │
│  Notification hook (notify-permission.sh):                   │
│    if notify=y: async call `vox synthesize` CLI directly     │
│    → audio plays immediately, no model involvement           │
│                                                              │
│  PostToolUse hook (suppress-output.sh):                      │
│    formats TTS MCP tool output for UI panel                  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
          │                                    │
          │ MCP tool calls                     │ CLI calls
          │                                    │
┌─────────▼────────────────────────────────────▼───────────────┐
│                    punt-vox Engine                            │
│                                                              │
│  vox mcp (stdio, thin client) ──► voxd :8421/ws (WebSocket) │
│  vox hook <event> (Python)   ──► voxd :8421/ws (WebSocket) │
│  vox unmute (CLI)            ──► voxd :8421/ws (WebSocket) │
│                                                              │
│  voxd: synthesis, playback queue, dedup, cache (DES-028)    │
│  Providers: ElevenLabs > OpenAI > Polly > say > espeak      │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## State Management

```text
.vox/config.md           # per-project, in project root
---
voice_enabled: false     # /voice toggle
notify: "n"              # /notify: y=on, c=continuous, n=off
speak: "y"               # /speak: y=voice, n=chime
---
```

All hooks and commands read this file for current state. The path is relative to the project root (cwd). See DES-012 for why this is per-project, not global.

---

## DES-001: Notification Architecture — Stop Hook with Decision Block

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** How task-completion notifications work

### Design

The Stop hook uses `decision: "block"` to make Claude generate one more turn with a spoken summary. This is the only mechanism available — the Stop hook does not support `additionalContext`.

**Flow:**

```text
Claude finishes → Stop hook fires → reads tts.local.md
  ├── notify=n → exit 0 (let stop, no notification)
  ├── stop_hook_active=true → exit 0 (prevent infinite loop)
  └── notify=y|c → return { decision: "block", reason: "..." }
        → Claude generates 1-2 sentence summary
        → Claude calls TTS speak tool (ephemeral, auto_play)
        → Claude stops → Stop hook fires again
        → stop_hook_active=true → exit 0 (done)
```

**The `reason` field is the prompt.** It tells Claude:

- Summarize what you just did in 1-2 sentences
- Call the TTS speak tool with ephemeral=true, auto_play=true
- Do not add any other commentary

### Why This Design

- The model understands what it just did and can generate intelligent summaries
- `last_assistant_message` is available but would require external summarization (API call) or dumb truncation if processed in shell only
- `stop_hook_active` provides a built-in infinite loop guard
- The extra model turn is minimal (1-2 sentences + tool call)

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Shell-only: extract + truncate `last_assistant_message`, call `tts synthesize` CLI | No intelligent summarization; shell truncation produces poor summaries |
| Async hook with CLI call | Cannot block the stop to get a summary; would only produce "task complete" with no context |
| `additionalContext` in Stop hook | Not supported — Stop hook only has `decision`/`reason` for control |

### UX Concern: Extra Model Turn

The user sees Claude generate one more message (the summary). This is acceptable because:

1. The summary is brief (1-2 sentences)
2. The audio plays while the user reads, not instead of reading
3. The skill prompt instructs minimal output

If this proves annoying, the fallback is Approach B: async shell-only with `tts synthesize "Task complete"` (no summary, just a notification).

---

## DES-002: Permission-Prompt Notification — Async CLI Call

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** How permission-prompt notifications work

### Design

The Notification hook (matcher: `permission_prompt`) fires an async shell command that calls the `tts` CLI directly. No model involvement.

**Flow:**

```text
Permission dialog appears → Notification hook fires (async)
  → reads tts.local.md
  ├── notify=n → exit 0
  ├── speak=n → play chime audio file
  └── speak=y → pick random phrase → vox synthesize "$TEXT" -o $TMPDIR/notify.mp3
```

### Why Async + CLI (Not Model)

- The notification message is already clear ("Claude needs permission to use Bash")
- No summarization needed — just announce it
- Async avoids blocking the permission dialog
- CLI call is fast and self-contained

### Why Not MCP Tool

The Notification hook runs outside the model's conversation. It cannot call MCP tools (those require the model to invoke them). The CLI is the correct interface for hook-initiated synthesis.

---

## DES-003: State File — Extended Config

**Date:** 2026-02-25
**Status:** SUPERSEDED by DES-012
**Topic:** Where notification and speech state is persisted

### Original Design (Superseded)

Originally used `~/.claude/tts.local.md` (global). Now uses `.vox/config.md` (per-project). See DES-012 for the migration rationale.

### Current Design

All TTS state in one file per project:

```yaml
---
voice_enabled: false
notify: "n"
speak: "y"
---
```

### Shell Parsing

Hooks parse YAML frontmatter with grep/sed — no YAML parser needed:

```bash
notify=$(grep '^notify:' "$STATE_FILE" | sed 's/notify: *"\?\([^"]*\)"\?/\1/')
```

This is fragile but adequate for flat key-value YAML. If the state file grows complex, migrate to a JSON sidecar.

---

## DES-004: /speak Toggle — Voice vs. Chime

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** How `/speak y` and `/speak n` control notification audio

### Design

`/speak y` = spoken words via TTS provider. `/speak n` = short audio tone (chime).

Chime audio is a pre-generated MP3 file bundled in the package. Two distinct tones:

- `chime_done.mp3` — task completed (pleasant, resolving)
- `chime_prompt.mp3` — needs approval (attention-getting, rising)

Played via `afplay` (macOS) directly from the hook script.

### Why Pre-Generated

- No API call needed for chimes — instant playback
- No provider dependency for chime-only mode
- Consistent sound regardless of provider availability

---

## DES-005: /recap — On-Demand Spoken Summary

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** How `/recap` generates and speaks a summary

### Design

`/recap` is a slash command (skill prompt) that instructs the model to:

1. Summarize the key points of its last response in 2-3 sentences
2. Call the TTS speak tool with the summary (ephemeral, auto_play)
3. Show the summary text in the conversation

### Why Skill Prompt (Not Hook)

- `/recap` is user-initiated, not event-driven
- The model needs to read its own context to summarize
- A skill prompt is the natural interface for "do something and speak it"

---

## DES-006: Plugin Hook Registration

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** How notification hooks are registered in the plugin

### Design

Hooks are declared in `hooks/hooks.json` and registered by the plugin system. The notification hooks are:

```json
{
  "Stop": [{
    "hooks": [{
      "type": "command",
      "command": "${CLAUDE_PLUGIN_ROOT}/hooks/notify.sh"
    }]
  }],
  "Notification": [
    {
      "matcher": "permission_prompt",
      "hooks": [{
        "type": "command",
        "command": "${CLAUDE_PLUGIN_ROOT}/hooks/notify-permission.sh",
        "async": true
      }]
    },
    {
      "matcher": "idle_prompt",
      "hooks": [{
        "type": "command",
        "command": "${CLAUDE_PLUGIN_ROOT}/hooks/notify-permission.sh",
        "async": true
      }]
    }
  ]
}
```

### Why Separate Scripts

- Stop hook (synchronous, returns JSON decision) has different logic than Notification hook (async, calls CLI)
- Separate scripts keep each handler focused and testable
- The permission and idle hooks share the same script (both announce a message)

---

## DES-007: MCP Tool Naming — Voice Domain Vocabulary

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** MCP tool names visible in the UI panel

### Design

Renamed the four MCP tools from clinical/technical names to voice/audio-themed names:

| Old (clinical) | New (on-brand) | Why |
|----------------|---------------|-----|
| `synthesize` | `speak` | The natural verb for giving voice to text |
| `synthesize_batch` | `chorus` | Multiple texts at once, like a chorus |
| `synthesize_pair` | `duet` | Two texts stitched together |
| `synthesize_pair_batch` | `ensemble` | Multiple pairs, like an ensemble |

CLI command names (`tts synthesize`, `tts batch`, etc.) and internal Python API are unchanged — only the MCP tool names visible in the UI.

### Why This Matters

MCP tool names appear in the tool-result panel every time a tool is called. "synthesize" reads like a chemistry lab; "speak" reads like what the plugin actually does. Follows the dungeon plugin pattern where `load`/`save`/`delete` became `recall`/`inscribe`/`obliterate`.

---

## DES-008: Two-Channel Display — Panel + Model Context

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** How MCP tool results display in the Claude Code UI

### Design

The PostToolUse hook (`suppress-output.sh`) splits tool output into two channels:

1. **`updatedMCPToolOutput`** — Compact panel line with `♪` prefix, voice, and provider:
   - `♪ "Hello world" — matilda (elevenlabs)`
   - `♪♪ 3 tracks — matilda (elevenlabs)`
   - `♪ "Hello | Hallo" — matilda+hans (elevenlabs)`
   - `♪♪ 5 pairs — matilda (elevenlabs)`

2. **`additionalContext`** — Full JSON result for the model to reference paths, metadata, etc.

Follows the two-channel display pattern from punt-kit/patterns/two-channel-display.md.

### Why `♪`

Biff uses `▶` as its visual glyph. `♪` (musical note) is the natural symbol for a voice/audio plugin — instantly recognizable, visually distinct from other plugins.

---

## DES-009: Notification Phrase Variation

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** How permission/idle notifications avoid repetitive phrasing

### Design

The notification hook (`notify-permission.sh`) selects from a pool of 7 natural-sounding phrases per notification type using bash `$RANDOM`. Avoids the robotic repetition of hearing "Needs your approval" every time.

Phrases are stored directly in the script (no external config). Selection uses a Bash 3.2-compatible `pick_random` function that takes array elements as positional arguments (no namerefs).

---

## DES-010: Plugin Install-or-Update — Never Leave Users on Old Versions

**Date:** 2026-02-26
**Status:** SETTLED
**Topic:** How `tts install` handles already-installed plugins

### Problem

`claude plugin install tts@punt-labs` returns non-zero with "already installed" when the plugin exists. Our installer treated this as success and moved on. Users on old versions had **no update path** — they were stuck unless they manually ran `claude plugin uninstall` + `install`.

This was invisible for a long time because the developer always has the latest (editable install). It only surfaced when a second machine ran the install script after a version bump.

### Design

Install follows an install-or-update pattern:

```text
claude plugin install tts@punt-labs
  ├── exit 0              → installed (fresh)
  ├── "already installed" → claude plugin update tts@punt-labs
  │     ├── exit 0           → updated
  │     ├── "up to date"     → already up to date (success)
  │     └── other error      → fail with message
  └── other error         → fail with message
```

### Key Details

- `_install_plugin()` calls `_update_plugin()` on the "already installed" path — single responsibility per function
- `_update_plugin()` handles three outcomes: updated, already current, error
- `install.sh` does not need changes — it calls `tts install` which delegates to `installer.py`
- The `claude plugin update` subcommand was discovered empirically via `claude plugin --help`; it is not documented in public Claude Code docs as of 2026-02-26

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Always uninstall then reinstall | Destructive; removes user's plugin state; slower |
| Tell users to manually update | Poor UX; they won't know to do it |
| Check version before install | No reliable way to query installed plugin version from CLI |

---

## DES-011: install.sh Under `set -eu` — Guard Expected Failures

**Date:** 2026-02-26
**Status:** SETTLED
**Topic:** How install.sh handles commands that may fail

### Problem

`install.sh` uses `set -eu` for safety. A bare command that exits non-zero kills the entire script before any error message can print:

```bash
set -eu
"$BINARY" install       # exits non-zero → script dies silently
INSTALL_EXIT=$?          # never reached
```

This was discovered when a user ran `install.sh` from a directory with no git repo. `tts install` (which runs `claude plugin install` → git clone) failed, and the script exited silently after "Setting up Claude Code plugin..." with no error message.

### Design

Wrap expected-failure commands in `if !` guards:

```bash
if ! "$BINARY" install; then
  fail "Plugin install failed"
fi
```

The `if` construct exempts the command from `set -e` — a non-zero exit runs the else branch instead of killing the script. This is POSIX-standard behavior.

### Rule

**Any command in `install.sh` that might legitimately fail must use `if !` or `||` to handle the failure path.** Bare commands are only safe for operations that should always succeed (like `printf`).

### Context

The user ran `install.sh` from a non-git directory. The SSH fallback added an HTTPS git rewrite, but `tts install` still failed (possibly because `claude plugin install` needs a working git clone and the environment was unusual). The silent exit meant the user had to manually diagnose and run `tts install` themselves.

---

## DES-012: Per-Project Config — `.vox/config.md` Not Global

**Date:** 2026-02-26
**Status:** SETTLED
**Topic:** Where TTS plugin state (notify, speak, voice) is stored

### Problem

The original state file was `~/.claude/tts.local.md` — a global path shared across all Claude Code sessions in all projects. Enabling `/notify y` in one project enabled it everywhere. This is wrong: notification preferences are per-project.

### Design

State file moved to `.vox/config.md` in the project root (cwd). Same YAML frontmatter format, same hook parsing — only the path changed.

```bash
# Before (global, leaked across projects)
TTS_STATE_FILE="$HOME/.claude/tts.local.md"

# After (per-project, isolated)
TTS_STATE_FILE=".vox/config.md"
```

### Why This Works

- `.vox/` is already in `.gitignore` (used for ephemeral audio output)
- Hooks run in the project root, so relative paths resolve correctly
- Follows the biff pattern: biff uses `.biff/` in the project root for per-project state
- Each project gets independent `/notify`, `/speak`, `/voice` settings

### Migration

No migration needed. The old global file is simply ignored. Users who had settings in `~/.claude/tts.local.md` start fresh per-project, which is the correct behavior (opt-in per project, not inherited globally).

---

## DES-013: Serialized Audio Playback via flock

**Date:** 2026-02-26
**Status:** SETTLED
**Topic:** How concurrent audio playback from MCP tools, Stop hook, and Notification hook is coordinated

### Problem

Three independent audio playback paths can fire simultaneously:

1. **MCP tools** — `speak`/`chorus`/`duet`/`ensemble` in `server.py` play audio after synthesis
2. **Stop hook** — `notify.sh` plays `chime_done.mp3` on task completion
3. **Notification hook** — `notify-permission.sh` plays chime or synthesized speech

When multiple paths fire at once, audio overlaps (cacophony). PR #17 attempted PID-based kill-previous — it prevented overlap but silenced the interrupted speaker.

### Design

Every playback invocation acquires `LOCK_EX` on `~/.punt-labs/vox/playback.lock`, runs `afplay` synchronously, then releases. Concurrent callers block on the lock and play in turn.

```text
Process A: flock(LOCK_EX) → afplay file1.mp3 → release
Process B:     [blocked]  ──────────────────→ flock(LOCK_EX) → afplay file2.mp3 → release
```

Two entry points in `playback.py`:

- `play_audio(path)` — blocking: flock → afplay → release
- `enqueue(path)` — non-blocking: spawn detached subprocess that calls `play_audio`

Bash hooks call `vox play <path>` (thin CLI wrapper). The MCP server calls `enqueue()` directly.

### Why fcntl.flock

- Zero infrastructure — no daemon, no message queue, no PID tracking
- Cross-process serialization — works across MCP server, hook scripts, CLI
- Self-cleaning — lock auto-releases on process exit, even crashes
- No audio killed — every utterance succeeds, just queued

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| PID-based kill-previous (PR #17) | Silences the interrupted speaker — user wants all utterances to succeed |
| Daemon with Unix socket | Operational complexity, lifecycle management, crash recovery |
| Named pipe (FIFO) | Requires a reader process; same daemon problem |
| No coordination (status quo) | Audio overlap is cacophonous |

### Platform Scope

`fcntl.flock` is POSIX (macOS + Linux). The audio player is resolved at
runtime: `afplay` (macOS native) → `ffplay` (cross-platform, from ffmpeg).
ffmpeg is already a project dependency (pydub uses it for audio processing).

---

## DES-014: Dev/Prod Namespace Isolation

**Date:** 2026-02-26
**Status:** SETTLED
**Topic:** How the plugin can be tested from the working tree alongside the installed production plugin

### Problem

`claude --plugin-dir .` loads the working tree as a plugin, but it collides with the installed production `vox` plugin if both use the same name. Developers cannot test plugin changes (hooks, commands, MCP tools) without uninstalling the production plugin first.

### Design

The working tree uses `"name": "vox-dev"` in `.claude-plugin/plugin.json`. Claude Code treats `vox` and `vox-dev` as separate plugins:

- **Prod tools**: `mcp__plugin_vox_vox__speak` (from installed plugin)
- **Dev tools**: `mcp__plugin_vox-dev_vox__speak` (from `--plugin-dir .`)

Dev commands (`say-dev.md`, `recap-dev.md`) in `.claude/commands/` reference dev-namespaced tools. Prod commands in `commands/` are unchanged.

The MCP server uses the installed `vox` binary as its command. With editable installs (`uv tool install --force --editable .`), the installed binary runs working-tree code — no `uv run` needed.

Release scripts (`scripts/release-plugin.sh`) swap `vox-dev` → `vox` and remove `*-dev.md` files before tagging. `scripts/restore-dev-plugin.sh` reverses this after tagging.

### Session-Start Hook Dispatch

The session-start hook detects dev mode by checking plugin.json for `"vox-dev"`:

- **Dev mode**: skip command deployment (prod plugin deploys top-level commands), auto-allow `mcp__plugin_vox-dev_vox__*`
- **Prod mode**: deploy commands to `~/.claude/commands/`, auto-allow `mcp__plugin_vox_vox__*`

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| `uv run` in plugin.json/hooks | Unacceptable production dependency — users may not have `uv` installed |
| Single namespace, uninstall prod to test | Destroys the production plugin; cannot test both simultaneously |
| Separate repo for dev plugin | Duplicates code; impossible to keep in sync |

### Key Invariant

The installed `vox` binary always runs working-tree code (via editable install). This means hooks, MCP server, and CLI all exercise the current source without `uv run`.

---

## DES-015: Marketplace Installs from HEAD, Not Tags

**Date:** 2026-02-27
**Status:** SETTLED
**Topic:** Why the v0.4.0 production install was broken and the systemic fix

### Root Cause

Claude Code marketplace installs clone HEAD of the default branch, not the version tag. When a marketplace entry has no `source.ref` field, `claude plugin install` resolves the `version` field for display only — the git clone targets HEAD.

This is invisible when HEAD and the tag are the same commit. It becomes a breaking defect when they diverge — which is exactly what dev/prod namespace isolation does. The release workflow pushes three commits in sequence:

```text
main:  ... → [release] → [prepare: name=vox] → [restore: name=vox-dev]
                              ↑ tag v0.4.0           ↑ HEAD
```

The tag points to the prepare commit (`name: "vox"`). HEAD points to the restore commit (`name: "vox-dev"`). The marketplace installs HEAD — so every user gets the dev plugin.

### Consequences of Installing the Dev Plugin

1. Plugin loads as `vox-dev`, not `vox`
2. Session-start hook detects `DEV_MODE=true`, skips command deployment
3. No top-level `/notify`, `/say`, `/speak`, `/recap`, `/voice` commands
4. User sees only namespaced commands: `/vox-dev:notify`, `/vox-dev:say`, etc.
5. Tool permission auto-allow writes the dev pattern (`mcp__plugin_vox-dev_vox__*`)

The plugin technically works — MCP server starts, audio plays — but the UX is wrong. The user has no idea they're running a dev build.

### Why This Wasn't Caught

1. The developer uses an editable install + `--plugin-dir .`, so the dev name is expected
2. The release script round-trip test verified the scripts work, not the installed artifact
3. No test installs from the marketplace after release — verification step 10 tests PyPI (`tts doctor`), not the plugin
4. Biff has the same dev/prod pattern but its HEAD happened to have the prod name at install time (no release had been cut since adding the pattern)

### Fix (Two Parts)

**Part 1: Pin `source.ref` in marketplace.json.**

Every marketplace entry must specify the release tag:

```json
{
  "name": "tts",
  "source": {
    "source": "github",
    "repo": "punt-labs/vox",
    "ref": "v0.4.0"
  },
  "version": "0.4.0"
}
```

This is required for any project where HEAD of main may diverge from the release tag — which is every project using dev/prod namespace isolation, and arguably every project where post-release commits exist.

**Part 2: Refresh the marketplace clone before plugin install.**

Pinning `source.ref` in the remote marketplace.json only helps when the local clone has the pin. Existing users whose marketplace clone predates the pin see the old marketplace.json without `source.ref` — and `claude plugin install` resolves HEAD again.

The installer must refresh the marketplace clone before running `claude plugin install`, using the supported CLI command:

```python
def _refresh_marketplace() -> StepResult:
    claude = shutil.which("claude")
    if not claude:
        return StepResult("Marketplace refresh", False, "claude CLI not found on PATH")
    result = subprocess.run(
        [claude, "plugin", "marketplace", "update", MARKETPLACE_KEY],
        capture_output=True, text=True, check=False,
    )
    ...
```

This uses `claude plugin marketplace update` rather than operating on the clone directly, consistent with DES-002 (CLI over config file editing). New users (no clone yet) get a fresh clone with current marketplace.json. Existing users get the latest `source.ref` pins before install.

### Rule

**Every marketplace entry MUST have `source.ref` pinned to the release tag.** The release workflow step 12 (marketplace bump) must update both `version` and `ref`. This is now documented in CLAUDE.md.

**Every installer MUST refresh the marketplace clone before `claude plugin install`.** The `_refresh_marketplace()` step runs `claude plugin marketplace update punt-labs` so existing users pick up ref pins from newer marketplace.json versions.

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Don't push restore commit to main | Breaks the dev workflow — developer's working tree would have prod name, defeating namespace isolation |
| Tag HEAD instead of the prepare commit | Tag would include dev artifacts; marketplace clones the tag and gets `vox-dev` anyway |
| File a Claude Code bug to resolve `version` → tag | Correct long-term fix, but we can't control Claude Code's release timeline; `ref` is the available mechanism now |
| Keep main always prod-ready, dev on branches | Every feature branch would need manual plugin.json swap; error-prone, defeats the automation |
| Only pin `source.ref`, skip refresh | Existing users with stale clones never see the pin — install still resolves HEAD |

### Discovery Chain

1. User installed v0.4.0, saw `/vox-dev:notify` instead of `/notify`
2. Checked installed plugin cache: `name: "vox-dev"`, commit `06c2ec7` (restore commit)
3. Compared to v0.4.0 tag: commit `c977c8c` (prepare commit), `name: "vox"`
4. Confirmed: marketplace installed HEAD, not tag
5. Added `source.ref: "v0.4.0"` to marketplace, nuked cache, reinstalled → `name: "tts"`, correct commit
6. Discovered stale clone problem: existing users whose clone predates the ref pin still get HEAD
7. Added `_refresh_marketplace()` to installer — pulls latest marketplace.json before install

---

## DES-016: Command Deployment Must Update, Not Skip-If-Exists

**Date:** 2026-03-05
**Status:** SETTLED
**Topic:** How SessionStart hook deploys top-level commands to `~/.claude/commands/`

### Problem

The SessionStart hook deployed commands with a skip-if-exists guard:

```bash
# WRONG: stale commands persist forever
if [[ ! -f "$dest" ]]; then
  cp "$cmd_file" "$dest"
fi
```

This meant that once a command file was deployed, it **never updated** — even across plugin upgrades and releases. Users accumulated stale command files with:

- Old `allowed-tools` (e.g., `Read`, `Write`, `Edit` instead of `Bash`)
- Old MCP tool names (e.g., `mcp__plugin_tts_vox__speak` from before the rename)
- Old implementation logic (prompt-driven config file editing instead of CLI calls)

The `/vox`, `/unmute`, `/mute`, `/vibe`, and `/recap` commands were all stale. Some still referenced tool names from 3+ releases ago.

### Why This Wasn't Caught

1. The developer uses `--plugin-dir .` (dev mode), which skips command deployment entirely
2. Editable install means the developer's `vox` binary runs working-tree code
3. The stale commands still "worked" — Claude could figure out the intent even with wrong tools — just not correctly
4. No integration test for "install plugin, upgrade, verify commands updated"

### Root Cause

The original skip-if-exists logic was written to be idempotent for first-run setup. The assumption was that commands don't change across releases. That assumption was wrong from day one — commands have changed in almost every release since the plugin launched.

### Fix

Compare content with `diff -q` and overwrite when different:

```bash
# CORRECT: update changed commands
mkdir -p "$COMMANDS_DIR"
if [[ ! -f "$dest" ]] || ! diff -q "$cmd_file" "$dest" >/dev/null 2>&1; then
  cp "$cmd_file" "$dest"
fi
```

### Scope

Fixed in all Punt Labs plugins:
- `vox/hooks/session-start.sh`
- `biff/hooks/session-start.sh`
- `dungeon/hooks/session-start.sh`

Updated in `punt-kit/standards/plugins.md` — the standard now mandates diff-and-update with correct/incorrect code examples.

### Rule

**SessionStart command deployment must always update stale files.** Never skip-if-exists for command deployment. The cost of an unnecessary copy is zero. The cost of a stale command is a broken user experience that persists across every session until the user manually deletes the file.

---

## DES-017: Call Path Performance — MCP over Bash, Hooks over LLM

**Date:** 2026-03-05
**Status:** SETTLED
**Topic:** Which call paths perform best for LLM-initiated and event-driven operations

### Benchmark

Measured 10 sequential calls through each path (apples-to-apples, one model
round-trip per call):

| Path | Avg per call | Why |
|------|-------------|-----|
| **LLM → MCP tool** | ~3.2s | Persistent stdio server, no process spawn. Response is structured JSON. |
| **LLM → Bash → CLI** | ~4.6s | Model round-trip + Python process spawn (~110ms) + text parsing. |
| **Shell hook → CLI** | ~110ms | No model involvement. Direct process execution. |

The model round-trip dominates both LLM paths (~3s of inference per call).
MCP wins over Bash because the server is already running (no spawn cost) and
returns structured data. Shell hooks calling CLI directly are ~30x faster
because they bypass the model entirely.

### Two fast paths

```text
Model-initiated:    LLM ──► MCP server (persistent, structured)
Event-driven:       Hook ──► CLI (no LLM, direct execution)
```

**LLM → MCP** for operations the model initiates: synthesis, voice queries,
config changes. The MCP server is a long-running process — zero startup cost,
structured JSON responses, PostToolUse hooks for UI formatting.

**Hook → CLI** for event-driven operations: stop notifications, permission
chimes, signal tracking. Shell hooks call `vox` CLI directly — no model
round-trip, no inference latency. The hook reads config with grep/sed and
calls the CLI in ~110ms total.

### The slow path (avoid)

```text
LLM ──► Bash ──► CLI    (worst of both worlds)
```

LLM → Bash → CLI combines model round-trip overhead with process spawn
overhead. Every Bash call spawns a fresh Python process (~110ms), and the
model still pays ~3s of inference to generate and parse the call. Use this
only when no MCP tool exists for the operation.

### The Read/Write antipattern (never)

```text
LLM ──► Read(.vox/config.md)    (file I/O through the model layer)
```

Never instruct the model to Read or Write config files directly. This
couples the model to file format details, bypasses the CLI's validation
logic, and is no faster than an MCP call. If the model needs config state,
either pass it in hook context (zero cost) or expose it via an MCP tool.

### Rule

**Model-initiated operations go through MCP. Event-driven operations go
through shell hooks calling CLI. The model should never touch config files
directly — use the CLI or MCP layer.**

---

## DES-018: Clean Stop Hook Reason — No Internal Data in User-Visible Output

**Date:** 2026-03-06
**Status:** SETTLED
**Topic:** What the Stop hook's `reason` field contains

### Problem

The Stop hook's `decision: "block"` response includes a `reason` field that Claude shows to the user as assistant output. Early implementations leaked internal state into this field:

```json
{"decision": "block", "reason": "♪ Saying my piece... | vibe_mode=auto vibe_tags=[calm] vibe_signals=tests-pass@14:32,lint-pass@14:33"}
```

The pipe-separated metadata was intended for the model to thread through to the `unmute` MCP tool. But the entire reason string is displayed in the chat, so users saw raw config data after every task completion.

### Design

The `reason` field contains **only** a `♪`-prefixed phrase — nothing else:

```json
{"decision": "block", "reason": "♪ Saying my piece..."}
```

Vibe tags are resolved deterministically from accumulated signals and written to `.vox/config.md` **before** the block response. When Claude calls `unmute` in the continuation turn, `apply_vibe()` reads tags from config automatically. No data passes through the reason string.

```text
Stop hook fires →
  1. resolve_tags_from_signals(config.vibe_signals) → "[relieved]"
  2. write_fields({"vibe_tags": "[relieved]", "vibe_signals": ""})  # atomic
  3. return {"decision": "block", "reason": "♪ Saying my piece..."}

Claude continues →
  4. Generates 1-2 sentence summary
  5. Calls unmute MCP tool → apply_vibe() reads vibe_tags from config
```

### Key Details

- **`resolve_tags_from_signals()`** maps signal counts and trajectory to 1-2 ElevenLabs expressive tags without LLM involvement. Deterministic: same signals always produce the same tags.
- **Signal consumption**: `write_fields()` atomically writes resolved tags AND clears `vibe_signals` in a single config update. This prevents signals from accumulating across stop cycles.
- **Vibe mode gating**: Auto mode resolves and writes tags. Manual mode with existing tags skips (user's choice preserved). Off mode skips entirely.

### Why Config-Mediated Tag Passing

The alternative — embedding tags in the reason string for Claude to extract and pass to the MCP tool — couples the user-visible output to internal data. Any change to tag format or signal structure changes what users see. Config-mediated passing decouples them completely: the hook writes to config, the MCP tool reads from config, and the reason string is free to be a simple human-friendly phrase.

### Rule

**The Stop hook `reason` field must contain only a user-friendly phrase.** No config data, no metadata, no pipe-separated fields. If the continuation turn needs data, write it to config before returning the block response.

---

## DES-019: Bluetooth Audio Lead-In Silence

**Date:** 2026-03-13
**Status:** SETTLED (current solution adequate; alternatives documented for future)
**Topic:** First syllable clipped on Bluetooth audio devices (AirPods)

### Problem

When playing TTS audio after a period of silence (2+ seconds), Bluetooth headphones (AirPods, others) clip the first ~300-500ms of audio. The user hears "...esting one two three" instead of "Testing one two three."

### Root Cause

Bluetooth A2DP audio devices enter a low-power state when no audio is playing. When audio suddenly starts, the Bluetooth controller needs ~300-500ms to:

1. Wake the radio from low-power mode
2. Re-negotiate the audio codec (AAC/SBC)
3. Fill the jitter buffer before playback begins

Audio frames transmitted during this wake-up window are dropped by the device. This is Bluetooth hardware behavior — not fixable in software without compensating for it.

### Why Vox Normally Masks This

In typical sessions, hooks fire frequently enough (chimes on permission prompts, quips on task completion, acknowledgment beeps) that the Bluetooth link stays in active mode. The gap between audio events is usually under 2 seconds — not long enough to trigger low-power transition.

The problem surfaces when there's a deliberate gap: recording → Scribe STT (~700ms) → TTS synthesis (~1000ms) → playback. That ~2 second silence is enough for AirPods to sleep.

### Current Solution

Prepend 500ms of silence to audio before playback:

```python
from pydub import AudioSegment
silence = AudioSegment.silent(duration=500)
speech = AudioSegment.from_mp3(audio_path)
combined = silence + speech
combined.export(padded_path, format="mp3")
```

The silence gives the Bluetooth controller something disposable to drop during wake-up. The user perceives no added latency because the 500ms would have been silent anyway (device was waking up).

### Scope

Currently applied only in the voice-loop spike script (`.tmp/spike-voice-loop.py`). Not yet integrated into the main playback pipeline (`playback.py`), which doesn't have this problem in normal usage because hooks keep the link warm.

### Future Alternatives

If the current approach proves insufficient or the problem surfaces in main playback:

| Approach | Mechanism | Trade-off |
|----------|-----------|-----------|
| **Inaudible keepalive** | Play sub-perceptible tone (~20Hz, -60dB) during synthesis gaps to keep the Bluetooth link active | Prevents the problem entirely; requires background audio thread; may affect battery |
| **CoreAudio device latency query** | Read `kAudioDevicePropertyLatency` and `kAudioDevicePropertySafetyOffset` via CoreAudio API to get actual device latency dynamically | Exact padding per device; macOS-only; requires PyObjC or ctypes bindings |
| **Bluetooth detection** | Query output device type via `sounddevice.query_devices()` or `system_profiler SPBluetoothDataType`; only pad when output is Bluetooth | No wasted silence on wired/built-in speakers; adds platform-specific detection logic |
| **Adaptive padding** | Start with 500ms, measure whether the first syllable is audible (via loopback or user feedback), adjust dynamically | Self-tuning; complex to implement; hard to measure "audibility" programmatically |

### Rule

**When playing audio after a gap of 2+ seconds, assume Bluetooth devices may need wake-up time.** The 500ms silence prefix is the simplest correct solution. Do not reduce it below 500ms without testing on AirPods.

---

## DES-020: Turn-Based Voice Conversation with Claude Code

**Date:** 2026-03-13
**Status:** PROPOSED
**Topic:** Architecture for voice input/output conversation loop in Claude Code

### Goal

Enable turn-based voice conversation with Claude: the user speaks instead of typing, Claude speaks its responses (already works via Stop hook + `/unmute`), and the loop repeats. Voice and keyboard coexist — the user can type a prompt at any time instead of speaking.

### The "Who Presses Enter?" Problem

Claude Code's turn model is user-initiated. Only the user typing and pressing Enter starts a new model turn. No plugin API exists to inject a user prompt programmatically. The voice transcript must somehow trigger Claude to start a new turn.

### Rejected Approaches

| Approach | Why Not |
|----------|---------|
| **Blocking MCP tool** (`listen` tool blocks until user speaks) | MCP tools shouldn't block indefinitely; ties up the tool call; doesn't fit the MCP interaction model |
| **`tools/list_changed` notification** | Delivers the transcript to Claude's tool list, but Claude is idle waiting for user input — the notification doesn't trigger a new turn |
| **User types "go" after speaking** | Defeats the purpose — speaking AND typing is worse than just typing |
| **Stop hook injects transcript as reason** | Only works at task completion boundaries; can't start a cold conversation with voice |

### Proposed Design: Background Task Notification Loop

The solution uses Claude Code's existing background task mechanism (`run_in_background`). A background process blocks until voice input is ready, then exits. Claude receives a `<task-notification>` with the transcript — this triggers a new model turn.

```text
┌─────────────────────────────────────────────────────────────────┐
│ Conversation Loop                                               │
│                                                                 │
│  Claude finishes task                                           │
│    → Stop hook: speaks summary (existing behavior)              │
│    → Claude spawns: `vox listen --wait` (run_in_background)     │
│    → Claude stops                                               │
│                                                                 │
│  User speaks in Lux panel whenever ready                        │
│    → Daemon: mic capture → Scribe STT → transcript ready        │
│    → `vox listen --wait` detects ready → prints transcript      │
│    → exits                                                      │
│                                                                 │
│  Claude receives <task-notification>                            │
│    → reads transcript from task output                          │
│    → acts on the instruction                                    │
│    → spawns next `vox listen --wait`                            │
│    → loop continues                                             │
└─────────────────────────────────────────────────────────────────┘
```

### Key Properties

- **No new Claude Code APIs.** Background tasks and task notifications are existing, proven mechanisms.
- **No timeout pressure.** The listener blocks until the user speaks — 5 seconds or 5 minutes.
- **Self-sustaining loop.** Each task completion spawns the next listener. Stops when voice mode is disabled.
- **Keyboard coexists.** If the user types a prompt before speaking, the background listener is killed or ignored. No conflict.
- **Only when enabled.** The listener is spawned only when voice input mode is active. Normal sessions are unaffected.

### Components

```text
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Lux Panel      │     │   Vox Daemon     │     │   Claude Code    │
│                  │     │   (mcp-proxy)    │     │                  │
│ [🎤 Record]      │────▶│ mic capture      │     │                  │
│ [⏹ Stop]        │     │ Scribe STT       │     │                  │
│                  │     │ transcript store  │     │                  │
│ Transcript:      │◀────│                  │     │                  │
│ "refactor auth"  │     │                  │     │                  │
│                  │     │                  │     │                  │
│ [Send] [Discard] │────▶│ ready=true       │     │                  │
│                  │     │                  │────▶│ task-notification │
└──────────────────┘     │                  │     │ "refactor auth"  │
                         │ `listen --wait`  │     │                  │
                         │ (blocks, then    │     │ → acts on it     │
                         │  prints + exits) │     │ → spawns next    │
                         └──────────────────┘     │   listener       │
                                                  └──────────────────┘
```

### `vox listen --wait` Command

Thin CLI command that:

1. Connects to the vox daemon (WebSocket or polling)
2. Blocks until a transcript is marked ready
3. Prints the transcript to stdout
4. Exits with code 0

If the daemon is unreachable or voice mode is disabled, exits immediately with code 1. No retry, no backoff — the caller (Claude) decides whether to respawn.

### Lux Voice Panel

The Lux window provides the visual interface for recording:

- **Record button** — starts mic capture via the daemon
- **Stop button** — ends recording, triggers Scribe transcription
- **Transcript display** — shows the transcribed text for review
- **Edit field** — user can correct Scribe mistakes before sending
- **Send button** — marks transcript as ready (unblocks `listen --wait`)
- **Discard button** — clears transcript, returns to idle state

### Prerequisites

| Dependency | Status |
|------------|--------|
| ElevenLabs Scribe STT | Proven in spike (700ms latency for 5s clip) |
| ElevenLabs TTS playback | Shipping (existing provider) |
| Bluetooth lead-in silence (DES-019) | Proven in spike (500ms padding) |
| Mic capture (`sounddevice`) | Proven in spike |
| Lux interactive elements | Available (buttons, text inputs, recv) |
| mcp-proxy daemon model | Shipped (DES-021) |
| `vox listen --wait` CLI command | Not built |
| Daemon-side transcript store | Not built |

### Phasing

1. **Spike (done):** Prove mic → Scribe → TTS round-trip works (`.tmp/spike-voice-loop.py`)
2. **mcp-proxy migration (done, DES-021):** Move vox to the daemon model so the daemon can own the mic, Scribe client, Lux connection, and transcript store
3. **`listen --wait` command:** CLI that blocks on the daemon's transcript-ready signal
4. **Lux voice panel:** Record/stop/send UI in the Lux window
5. **Conversation loop integration:** Stop hook spawns `listen --wait` in background when voice mode is active

### Open Questions

1. **How does the first turn start?** The loop is self-sustaining once running, but the initial `listen --wait` needs to be spawned. A `/voice` command could spawn the first listener.
2. **Concurrent listeners:** If the user types a prompt while `listen --wait` is running, the background task should be killed or its result ignored. How does Claude handle a stale task notification that arrives after a typed prompt?
3. **VAD vs button:** The Lux panel uses explicit Record/Stop buttons. Future enhancement: VAD-based auto-detection (start recording on speech, stop on silence) for hands-free operation.
4. **Error recovery:** If Scribe fails or returns garbage, the user sees it in the Lux panel and can discard. But `listen --wait` should also handle daemon disconnection gracefully.
5. **Multi-session:** With the daemon model, multiple Claude Code sessions could have voice mode enabled. The daemon needs per-session transcript state (keyed by session_key from mcp-proxy).

---

## DES-021: Daemon Mode — Single Process with mcp-proxy

**Date:** 2026-03-14
**Status:** SUPERSEDED by DES-028

> **Note:** This ADR describes the v2 mcp-proxy daemon design. The production implementation now uses `voxd` (DES-028) — a simpler audio server without mcp-proxy, ContextVar, or PID-based CWD resolution.

**Topic:** Convert vox from per-session MCP processes to a single daemon

### Problem

Each Claude Code session spawns its own `vox mcp` process (~19MB each). With 10+ sessions, that's 10+ independent processes, each with its own TTS provider, playback queue, and hook handlers. Three concrete problems:

1. **Duplicate audio**: `biff wall` sends the same notification to all sessions → each synthesizes and plays identical TTS independently
2. **Resource waste**: 10+ Python processes doing the same work
3. **Hook latency**: Each hook invocation cold-starts Python (~500ms) to call `vox hook <event>`

### Design

Single long-running daemon fronted by mcp-proxy (same pattern as quarry, DES-020 prerequisite):

```text
MCP bridge (long-lived, per-session):
                    stdio                      WebSocket
Claude Code ◄──────────────► mcp-proxy ◄──────────────────────► vox serve
             MCP JSON-RPC    (~6MB Go)       ws://localhost:8421  (one daemon)
                                              /mcp

Hook relay (one-shot, per-event):
                    stdin/stdout                WebSocket
Hook script ──────────────────► mcp-proxy ──────────────────────► vox serve
             JSON payload       (~15ms)        ws://localhost:8421  (same daemon)
                                               /hook
```

Falls back to `vox mcp` (stdio) and `vox hook <event>` (subprocess) when daemon/mcp-proxy unavailable.

### Key decisions

**Starlette ASGI over plain WebSocket server** — Reuses the pattern from quarry's `http_server.py`. Starlette provides routing, lifespan management, and test client support. uvicorn handles signal handling and graceful shutdown.

**ContextVar for per-session config isolation** — Each MCP WebSocket connection sets `_config_path_override` via ContextVar so `resolve_config_path()` returns the correct project's `.vox/config.md` without passing paths through every function. The ContextVar is reset when the connection closes.

**CWD resolution from PID** — When a session connects with `?session_key=<pid>`, the daemon looks up the process's cwd via `lsof` (macOS) or `/proc/<pid>/cwd` (Linux) to find the right `.vox/config.md`. This is resolved once and cached in the session registry.

**Audio deduplication** — `DaemonContext.should_play(cache_key)` returns False if the same notification type was played within 5 seconds. Checked on the event loop thread (before `asyncio.to_thread` dispatch) to avoid data races. Prevents biff-wall duplicate audio across sessions.

**Graceful fallback** — Plugin.json uses `sh -c "if command -v mcp-proxy; then exec mcp-proxy ws://...; else exec vox mcp; fi"`. Hook scripts try `mcp-proxy --hook` first, fall back to `vox hook <event>`. Users without mcp-proxy or without the daemon running get identical behavior to before.

### Alternatives considered

1. **Unix domain socket instead of WebSocket** — Simpler but mcp-proxy speaks WebSocket natively. UDS would require a custom transport.
2. **Shared process group** — Use multiprocessing to share state. Too fragile across crash/restart cycles.
3. **Redis/IPC for dedup** — Over-engineered. The daemon is single-process; a dict with monotonic timestamps is sufficient.

### Files

- `src/punt_vox/daemon.py` — Starlette app with /mcp, /hook, /health
- `src/punt_vox/service.py` — launchd/systemd service management
- `src/punt_vox/config.py` — Added `_config_path_override` ContextVar
- `src/punt_vox/server.py` — Added `run_mcp_session()` for WebSocket transport
- `src/punt_vox/__main__.py` — `vox serve`, `vox daemon install/uninstall/status`
- `.claude-plugin/plugin.json` — mcp-proxy fallback
- `hooks/*.sh` — Daemon-first relay with subprocess fallback

---

## DES-022: AskUserQuestion Works Inside Slash Commands

**Date:** 2026-03-20
**Status:** SETTLED (verified, test artifact removed)
**Topic:** Whether `AskUserQuestion` renders inside skill/command execution

### Finding

A test command (`commands/ask-test-dev.md`) verified that `AskUserQuestion` with options renders correctly inside a slash command. The tool presents a picker UI and returns the selected option. This confirms commands can use interactive prompts for user input, not just static instructions.

### Outcome

Test passed. The `ask-test-dev.md` scaffold was removed — it served its purpose and has no production value. Commands that need user choices (e.g., voice selection in `/unmute`) can use `AskUserQuestion` with confidence.

---

## DES-023: Assets Bundled in Python Package

**Date:** 2026-03-28
**Status:** SETTLED
**Topic:** How chime MP3 files are distributed and resolved at runtime

### Problem

Chime audio never played in daemon mode or from the installed `vox` binary. `_resolve_assets_dir()` had two strategies:

1. `CLAUDE_PLUGIN_ROOT` env var → `$CLAUDE_PLUGIN_ROOT/assets/` — works for Claude Code hook scripts
2. `Path(__file__).parent.parent.parent / "assets"` → walks up to repo root — works for editable installs from the source tree

Strategy 2 fails for the installed package: `__file__` resolves to `site-packages/punt_vox/hooks.py`, so `.parent.parent.parent` = `site-packages/`, and `site-packages/assets/` doesn't exist. The daemon process runs from the installed binary with no `CLAUDE_PLUGIN_ROOT`, so every chime resolution failed silently ("missing chime_done.mp3" in logs).

### Design

Move canonical assets into the Python package: `assets/` → `src/punt_vox/assets/` (subpackage with `__init__.py`). `uv_build` auto-discovers and includes them in the wheel.

Fallback path becomes `Path(__file__).resolve().parent / "assets"` — sibling to the module files. Works for editable installs, installed packages, and daemon mode.

A symlink at repo root (`assets` → `src/punt_vox/assets`) preserves the `CLAUDE_PLUGIN_ROOT/assets/` resolution path for Claude Code hook scripts.

### Why Subpackage, Not `data` Config

`uv_build`'s `data` directive installs files into a platform-specific `.data/` directory in the wheel, not alongside the Python modules. Files there aren't findable via `__file__`-relative paths. Making `assets/` a subpackage (with `__init__.py`) puts the MP3s directly in `site-packages/punt_vox/assets/`, co-located and trivially resolvable.

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| `importlib.resources` | Adds complexity; `Path(__file__).parent / "assets"` is simpler and works for all cases |
| Copy assets at install time via post-install hook | `uv` doesn't support post-install hooks; fragile |
| Resolve from Claude plugin installation directory | Fragile — depends on knowing the plugin install path, which varies |
| Keep assets at repo root, fix `__file__` traversal | Three `.parent` calls is fragile and breaks when package layout changes |

---

## DES-024: Daemon Lifecycle — Kill Process on Uninstall, Detect Stale on Install

**Date:** 2026-03-28
**Status:** SETTLED
**Topic:** How `vox daemon install` and `vox daemon uninstall` handle running processes

### Problem

Two lifecycle bugs discovered while deploying the DES-023 asset fix:

1. **`vox daemon uninstall`** removed the launchd plist but left the daemon process running. The old process continued serving on port 8421 with pre-fix code, invisible to the user.

2. **`vox daemon install`** did not detect a stale process occupying port 8421. `launchctl load` failed silently (or the new process couldn't bind), but `_launchd_status()` showed the service as "loaded" — so install reported success while the old process kept running.

Both bugs compound: uninstall leaves a zombie, install doesn't detect it, user thinks they upgraded but nothing changed.

### Design

A shared `_kill_stale_daemon()` helper used by both install and uninstall:

```text
_kill_stale_daemon():
  1. Read port from ~/.punt-labs/vox/serve.port (fallback: DEFAULT_PORT)
  2. Find PID via lsof -ti :<port> (macOS) or fuser <port>/tcp (Linux)
  3. SIGTERM → wait up to 5s → SIGKILL if still alive
  4. Remove serve.port (serve.token is preserved for session continuity)
```

- **Uninstall** calls `_kill_stale_daemon()` after removing the service config
- **Install** calls `_kill_stale_daemon()` before registering the new service

### Why SIGTERM-then-SIGKILL

The daemon runs a Starlette ASGI server with active WebSocket connections. SIGTERM triggers uvicorn's graceful shutdown (closes connections, runs lifespan shutdown). SIGKILL is the fallback for hung processes — 5 seconds is generous for a local daemon with no persistent state.

### Why Not Just `launchctl kickstart`

`launchctl kickstart -k` can restart a service, but it requires the service to be loaded. If the plist was removed (uninstall) or never loaded (stale process from a previous install), kickstart has nothing to act on. Directly killing the process is the only reliable approach.

## DES-025: Daemon Provider Key Resolution via keys.env

**Date:** 2026-03-28
**Status:** SETTLED
**Topic:** How the daemon gets API keys for TTS providers when launchd/systemd strip the shell environment

### Problem

The vox daemon runs as a launchd (macOS) or systemd (Linux) service. These init systems start processes with a minimal environment — no direnv, no shell profile, no API keys. Without `ELEVENLABS_API_KEY` or `OPENAI_API_KEY`, the daemon falls back to `say` (macOS) or `espeak` (Linux) — system TTS that sounds terrible.

Before the daemon existed, vox ran inside Claude Code's process and inherited the shell environment. The daemon broke that model.

### Rejected Alternatives

1. **Embed API keys in the launchd plist / systemd unit** — Keys would be visible in the service config file. More importantly, the plist is written at install time from whatever shell runs `vox daemon install`. If that shell doesn't have direnv loaded (e.g., running from a directory without `.envrc`), no keys get embedded. Fragile and non-obvious.

2. **Pass keys per-request via MCP protocol** — Would preserve the "your shell controls your provider" model, but adds complexity to the MCP wire protocol, requires changes to every MCP tool, and means the daemon can't auto-detect providers at startup. Also raises questions about key transit security over localhost WebSocket.

3. **Read keys from macOS Keychain / Linux secret-service** — Not portable. Keychain is user-specific setup, not something every vox user would have. Not a general solution.

### Design

A dedicated config file at the system config directory — `$(brew --prefix)/etc/vox/keys.env` on macOS, `/etc/vox/keys.env` on Linux. Simple `KEY=VALUE` format, chmod 0600.

**Write path:** `sudo vox daemon install` calls `_write_keys_env()` in service.py. This snapshots provider-relevant env vars (`ELEVENLABS_API_KEY`, `OPENAI_API_KEY`, `AWS_*`, `TTS_PROVIDER`, `TTS_MODEL`) from the caller's shell into the system config file.

**Read path:** `voxd` calls `_load_keys()` at startup, before logging or provider auto-detection. Sets `os.environ` for keys not already present. This means:
- launchd/systemd daemon: loads all keys from file (nothing in env)
- Manual `voxd` from shell with direnv: env vars already set, file keys ignored

**Resolution order:** shell env var > keys.env value > provider unavailable.

### Why a Flat File, Not TOML/YAML

The file is never sourced by a shell. It's parsed by a 15-line Python function. No quoting, no escaping, no schema. One `KEY=VALUE` per line, `#` comments, blank lines ignored. The simplest format that works.

## DES-026: Stable Auth Token Across Daemon Restarts

**Status:** SETTLED

### Problem

The daemon generated a fresh auth token (`secrets.token_urlsafe(32)`) on every startup. Clients already connected held the old token. When the daemon restarted, all existing client connections failed authentication.

### Decision

The auth token is generated once and persisted to `<run_dir>/serve.token` (chmod 0600). It is stable across daemon restarts. In v3, the run dir is a system path: `$(brew --prefix)/var/run/vox/` on macOS, `/var/run/vox/` on Linux.

- `voxd` startup (`_read_or_create_token()`): reads the token from file. If the file is missing, generates and persists one.
- The token file is NOT removed on daemon shutdown (unlike `serve.port`, which is removed to signal the daemon is down).

### Why Not Remove Auth Entirely

The daemon binds to `127.0.0.1`, so only local processes can connect. Removing auth was considered (same trust model as Docker daemon default, Redis default). Rejected because:

1. Multi-user systems: other users on the same machine could connect.
2. Defense in depth: the token costs nothing and prevents accidental tool invocation by other local MCP clients.
3. The token mechanism already exists and works — removing it is churn with no upside.

### Rejected: Token Rotation on Every Install

The original design (regenerate on install) was simpler but broke the reconnection invariant. mcp-proxy's reconnect logic (exponential backoff, caps at 5s) works correctly only when the URL is stable. Changing the token on install means the daemon must either: (a) accept both old and new tokens during a grace period, or (b) require all clients to re-read the token file. Both are more complex than simply keeping the token stable.

## DES-027: Data Directory Migration to ~/.punt-labs/vox/

**Status:** SUPERSEDED by DES-028

> **Note:** DES-027 migrated daemon data from `~/.punt-vox/` to `~/.punt-labs/vox/`. DES-028 (v3) subsequently moved all daemon data to system paths: `$(brew --prefix)/etc/vox/`, `$(brew --prefix)/var/run/vox/`, etc. on macOS; FHS paths on Linux. Home directory paths are now client-side only (`.vox/config.md` in project dirs).

## DES-028: Vox v3 — Audio Server Architecture

**Status:** SETTLED

### Problem

The v2 daemon tried to know which project a client belonged to. It resolved CWDs from PIDs via `lsof`, read/wrote `.vox/config.md` in project directories, and used ContextVars to isolate per-session config. Every piece of this chain broke — 8 rounds of path bugs. The root cause was architecture, not code.

### Decision

One machine, one set of speakers, one audio daemon (`voxd`). Clients send text + parameters. The daemon synthesizes and plays. It knows nothing about projects, sessions, CWDs, or Claude Code.

**Two entry points, one package:**
- `voxd` — system-level audio daemon. Owns speakers, providers, playback queue, dedup, cache. System paths (Homebrew prefix on macOS, FHS on Linux).
- `vox` — everything else. CLI, MCP server (`vox mcp`), hook handlers. All are clients of `voxd`.

**Wire protocol:** WebSocket + JSON messages. Streaming-capable for future real-time voice conversation.

**MCP server:** Lightweight stdio process per Claude Code session. Session state in memory. Finds `.vox/config.md` by walking up from CWD (same as biff). Calls `voxd` via WebSocket for synthesis/playback. No provider imports — cold start < 500ms.

**Hooks:** Three-layer dispatch unchanged (hooks.md standard). Python handlers call `voxd` via WebSocket client. No in-process synthesis.

**Service install:** System-level. macOS: `/Library/LaunchDaemons/` with `UserName` = installing user. Linux: `/etc/systemd/system/` with `User` = installing user. Requires sudo.

### Why Not Keep the Proxy Architecture

mcp-proxy existed to avoid spawning a Python process per session. The new MCP server is lightweight (no provider imports) so Python startup cost is acceptable. Eliminating mcp-proxy removes a Go binary dependency, the WebSocket MCP bridge, and the entire class of "MCP session doesn't survive daemon restart" bugs.

### Why WebSocket, Not HTTP

HTTP request/response can't do bidirectional streaming. Real-time voice conversation (vox-7hr) needs streaming audio in both directions. WebSocket handles both fire-and-forget synthesis (today) and streaming conversation (future) without a protocol change.

### System Paths

| Purpose | macOS (Homebrew) | Linux |
|---------|-----------------|-------|
| Config | `$(brew --prefix)/etc/vox/keys.env` | `/etc/vox/keys.env` |
| Cache | `$(brew --prefix)/var/lib/vox/cache/` | `/var/lib/vox/cache/` |
| Logs | `$(brew --prefix)/var/log/vox/voxd.log` | `/var/log/vox/voxd.log` |
| Runtime | `$(brew --prefix)/var/run/vox/serve.{port,token}` | `/var/run/vox/serve.{port,token}` |
| Service | `/Library/LaunchDaemons/com.punt-labs.voxd.plist` | `/etc/systemd/system/voxd.service` |

### Why System Paths, Not Home Directory

The daemon serves the machine, not a user. One set of speakers. Data belongs in system directories (`/Library/LaunchDaemons/`, Homebrew `var/`, FHS `/var/`). Home directory paths caused the v2 path resolution bugs — the daemon's CWD was `/` under launchd but all paths assumed `~`.

### Service Identity

`voxd` runs as the installing user, not root. Audio device access (CoreAudio on macOS, PulseAudio/PipeWire on Linux) is tied to the desktop session user. The LaunchDaemon plist sets `UserName` to `$SUDO_USER`; the systemd unit sets `User=` to `$SUDO_USER`. The service install requires sudo only because the plist/unit file goes in a system directory — the daemon process itself has normal user privileges.
