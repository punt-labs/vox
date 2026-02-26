# punt-tts Design Decision Log

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
│    if notify=y: async call `tts synthesize` CLI directly     │
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
│                    punt-tts Engine                            │
│                                                              │
│  MCP Server (speak, chorus, duet, ensemble)                  │
│  CLI (tts synthesize "text" --ephemeral --auto-play)         │
│  Providers: ElevenLabs > OpenAI > Polly (auto-detect)        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## State Management

```text
.tts/config.md           # per-project, in project root
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
  └── speak=y → pick random phrase → tts synthesize "$TEXT" -o $TMPDIR/notify.mp3
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

Originally used `~/.claude/tts.local.md` (global). Now uses `.tts/config.md` (per-project). See DES-012 for the migration rationale.

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

## DES-012: Per-Project Config — `.tts/config.md` Not Global

**Date:** 2026-02-26
**Status:** SETTLED
**Topic:** Where TTS plugin state (notify, speak, voice) is stored

### Problem

The original state file was `~/.claude/tts.local.md` — a global path shared across all Claude Code sessions in all projects. Enabling `/notify y` in one project enabled it everywhere. This is wrong: notification preferences are per-project.

### Design

State file moved to `.tts/config.md` in the project root (cwd). Same YAML frontmatter format, same hook parsing — only the path changed.

```bash
# Before (global, leaked across projects)
TTS_STATE_FILE="$HOME/.claude/tts.local.md"

# After (per-project, isolated)
TTS_STATE_FILE=".tts/config.md"
```

### Why This Works

- `.tts/` is already in `.gitignore` (used for ephemeral audio output)
- Hooks run in the project root, so relative paths resolve correctly
- Follows the biff pattern: biff uses `.biff/` in the project root for per-project state
- Each project gets independent `/notify`, `/speak`, `/voice` settings

### Migration

No migration needed. The old global file is simply ignored. Users who had settings in `~/.claude/tts.local.md` start fresh per-project, which is the correct behavior (opt-in per project, not inherited globally).
