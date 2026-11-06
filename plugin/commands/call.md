---
description: "Start or stop a Conversation Mode call with a read-only call agent"
argument-hint: "start <topic> [--script <path>] | stop"
allowed-tools: ["Bash(vox call:*)", "Read", "mcp__quarry__find", "mcp__plugin_quarry_quarry__find"]
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

   Before writing it, search with the quarry MCP `find` tool (a query drawn
   from the topic) and fold the relevant hits in: what each says, and its
   source path. Never put the query on a shell command line -- topic text in
   a shell string can run as a command. Look up external library docs as well
   if the topic needs them. Keep the snapshot to
   a briefing -- a page or two, never whole files, never a secret value.

2. **Start the call in the background**, with the snapshot on stdin through a
   quoted heredoc so nothing in it is expanded by the shell. The heredoc
   delimiter is fresh for every call: `VOX_CONTEXT_` followed by 8 hex digits
   you choose at random now -- never a value written in this file -- so no text
   folded into the snapshot -- a quarry hit, a file excerpt -- can contain the
   line that ends it early and runs what follows as shell. Check that no line
   of the snapshot is exactly the delimiter; if one is, pick another. Run this
   Bash command with `run_in_background: true` -- `vox call start` runs for
   the whole call:

   ```bash
   vox call start --context - <<'VOX_CONTEXT_<8 random hex digits>'
   <the snapshot>
   VOX_CONTEXT_<the same 8 hex digits>
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
its whole life: `--tools read,grep,find,ls`, no MCP, no skills, no prompt
templates, no project-local pi configuration, no repo context files, and no
extensions but one: vox's own secret guard. Its environment is an allowlist
(`PATH`, `HOME`, locale, terminal, temp and XDG directories, `PI_*`), so no
token or key from your shell reaches it; pi authenticates from its own
`~/.pi/agent` configuration. Before any tool runs, the guard checks the path
every read, grep, find, and ls names: it must lie inside the repository and
must not match a secret pattern (`.env*`, key files, `.git/`,
`.git-credentials`, and the secret-looking patterns in the repo's
`.gitignore`), and in a git repository it must not be a
path git ignores -- asked of git itself, so a `.gitignore` at any depth,
`.git/info/exclude`, and your global excludes file all count; if git cannot
answer, the read is refused. Grep globs and find patterns are checked too. A
grep over a directory is allowed only after the guard lists the files ripgrep
would search there (same binary, same ignore rules, same glob) and finds none
that is denied; if it cannot list them, the grep is refused. A grep with no
glob of its own is narrowed to skip `.git/`. On a
refusal the agent tells the caller it can't open that. find and ls can still
reveal file *names* inside the repository, never contents. If a denied read
ever ran anyway, the call ends before a word of the reply is spoken.

Summaries are written to `~/.punt-labs/vox/calls/<call-id>-summary.md`; the
newest 50 are kept.

While a call is active, `plugin/hooks/call-lock.sh` blocks the human's own
interactive prompts, self-clearing if the holding process has died.
`/call stop` passes the lock unconditionally.
