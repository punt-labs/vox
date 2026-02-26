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
│    → Claude generates 1-2 sentence summary + calls synth     │
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
│  MCP Server (synthesize, batch, pair, pair-batch)            │
│  CLI (tts synthesize "text" --ephemeral --auto-play)         │
│  Providers: ElevenLabs > OpenAI > Polly (auto-detect)        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## State Management

```text
~/.claude/tts.local.md
---
voice_enabled: false     # /voice toggle (existing)
notify: "n"              # /notify: y=on, c=continuous, n=off
speak: "y"               # /speak: y=voice, n=chime
---
```

All hooks and commands read this file for current state.

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
        → Claude calls TTS synthesize tool (ephemeral, auto_play)
        → Claude stops → Stop hook fires again
        → stop_hook_active=true → exit 0 (done)
```

**The `reason` field is the prompt.** It tells Claude:

- Summarize what you just did in 1-2 sentences
- Call the TTS synthesize tool with ephemeral=true, auto_play=true
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
  └── speak=y → tts synthesize "Needs your approval" --ephemeral --auto-play
```

### Why Async + CLI (Not Model)

- The notification message is already clear ("Claude needs permission to use Bash")
- No summarization needed — just announce it
- Async avoids blocking the permission dialog
- CLI call is fast and self-contained

### Why Not MCP Tool

The Notification hook runs outside the model's conversation. It cannot call MCP tools (those require the model to invoke them). The CLI is the correct interface for hook-initiated synthesis.

---

## DES-003: State File — Extended tts.local.md

**Date:** 2026-02-25
**Status:** SETTLED
**Topic:** Where notification and speech state is persisted

### Design

Extend the existing `~/.claude/tts.local.md` (already used by `/voice`) with `notify` and `speak` fields:

```yaml
---
voice_enabled: false
notify: "n"
speak: "y"
---
```

### Why Single File

- `/voice` already reads/writes this file
- All TTS plugin state in one place
- Hooks read one file, not multiple
- YAML frontmatter is the established pattern (biff uses `.claude/biff.local.md`)

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
2. Call the TTS synthesize tool with the summary (ephemeral, auto_play)
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
