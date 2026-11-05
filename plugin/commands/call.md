---
description: "Start or stop a Conversation Mode call with a read-only call agent"
argument-hint: "start <topic> [--script <path>] | stop"
allowed-tools: ["Bash(vox call:*)", "Bash(quarry:*)", "Read"]
---

# /call command

Conversation Mode: a live voice call between the human and a call agent,
mediated by `voxd`. The call agent is a separate, read-only process. It cannot
see this session, so you hand it a context snapshot when the call starts, and
it hands you a written summary when the call ends. There is no `mic:call` MCP
tool yet -- every verb shells out to the `vox call` CLI.

## Usage

- `/call start <topic>` -- start a real call about `<topic>`: real microphone
  capture, transcribed by ElevenLabs.
- `/call start <topic> --script <path>` -- dev/test path: scripted turns from
  a JSON Lines file (`{"text": ..., "confidence": ...}` per line) instead of
  the microphone. For demos and CI.
- `/call stop` -- ask the running call to hang up.

## Implementation

### start

1. **Write the context snapshot yourself.** You are the primary session; the
   call agent starts with nothing but what you give it. In plain prose, cover:
   - the topic (`<topic>` from `$ARGUMENTS`) and what the human wants from
     the call;
   - the current task: what you are working on, the branch, any bead ID;
   - the files that matter, by path;
   - the gist of the recent conversation: decisions made, open questions.

   Before writing it, run `quarry --json find "<a query from the topic>"` and
   fold the relevant hits in (what each says, and its source path). Look up
   external library docs as well if the topic needs them. Keep the snapshot to
   a briefing -- a page or two, never whole files, never a secret value.

2. **Start the call in the background**, with the snapshot on stdin through a
   quoted heredoc so nothing in it is expanded by the shell. Run this Bash
   command with `run_in_background: true` -- `vox call start` runs for the
   whole call:

   ```bash
   vox call start --context - <<'VOX_CONTEXT'
   <the snapshot>
   VOX_CONTEXT
   ```

   Append `--script <path>` verbatim when it was given.

3. Tell the human the call is starting, then end your turn. Do not poll.

4. **When the background command exits**, its last line is either
   `call summary: <path>` or `no call summary: <reason>`. Read the summary
   file and report its Decisions Made and Action Items to the human. The
   Action Items are repository changes the call agent could not make itself;
   confirm with the human before carrying them out.

   If the command exited with an error instead, report the error from its
   output.

### stop

Run `vox call stop`. It is a fire-and-forget signal to the running
`vox call start`, through the cross-process control file
(`src/punt_vox/voxd/conversation_mode/call_control.py`); the call then ends
cleanly and writes its summary.

## What the call agent is

One `pi --mode rpc` process, spawned when the call starts and held until it
ends, so each turn reuses the warm context of the last. It runs read-only for
its whole life: `--tools read,grep,find,ls`, no extensions, no MCP, no skills,
no project-local pi configuration. The ElevenLabs, OpenAI, and AWS keys
`voxd` holds are stripped from its environment. Paths that may hold secrets
(`.env*`, key files, and the secret-looking patterns in the repo's
`.gitignore`) are off limits: the rule is in its system prompt, and any
attempt to touch one ends the call before a word of the reply is spoken.

Summaries are written to `~/.punt-labs/vox/calls/<call-id>-summary.md`; the
newest 50 are kept.

While a call is active, `plugin/hooks/call-lock.sh` blocks the human's own
interactive prompts, self-clearing if the holding process has died.
`/call stop` passes the lock unconditionally.
