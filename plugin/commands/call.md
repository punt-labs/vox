---
description: "Talk to voxd's call agent, which delegates coding to workers; or start the read-only pi call"
argument-hint: "--headset | --aec | --script <path> | status | stop | start <topic> [--script <path>]"
allowed-tools: ["Bash(vox call:*)", "Read", "mcp__quarry__find", "mcp__plugin_quarry_quarry__find"]
---

# /call command

Conversation Mode: a live voice call between the human and a call agent,
mediated by `voxd`. There are two call agents, and every verb shells out to
the `vox call` CLI (there is no `mic:call` MCP tool yet):

- **voxd's call agent** (`/call --headset`): an ElevenLabs agent that `voxd` owns.
  It starts, steers, checks and stops persistent coding workers for the human,
  and the workers keep running after the call ends.
- **The pi call agent** (`/call start <topic>`): a separate, read-only process.
  It cannot see this session, so you hand it a context snapshot when the call
  starts, and it hands you a written summary when the call ends. Mode B
  replaces it in a later release.

## Usage

- `/call --headset` -- talk to voxd's call agent while wearing a headset.
- `/call --aec` -- use a system echo-cancelled microphone and speaker path
  you have already configured. Vox does not set up echo cancellation.
- `/call --script <path>` -- dev/test path: scripted turns from a JSON Lines
  file of recorded speech (`{"pcm_path": ..., "pause_s": ...}` per line: 16 kHz
  mono S16LE PCM, a relative path read from the script's directory) instead of
  the microphone. For demos and CI.
- `/call status` -- show voxd's call: its state, transcript and tool calls.
- `/call start <topic>` -- start a pi call about `<topic>`: real microphone
  capture, transcribed by ElevenLabs.
- `/call start <topic> --script <path>` -- dev/test path for the pi call:
  scripted turns from a JSON Lines file (`{"text": ..., "confidence": ...}`
  per line) instead of the microphone.
- `/call stop` -- ask the running call to hang up.

## Implementation

### call (no verb)

1. If there is no `--headset`, `--aec` or `--script`, ask the human to wear
   a headset and rerun `/call --headset` (or use `/call --aec` if their system
   audio path already cancels echo). Do not silently add a declaration.
   Otherwise run the corresponding command with `run_in_background: true`;
   `vox call` runs for the whole call:

   ```bash
   vox call --headset
   ```

   Use `vox call --aec` or `vox call --script '<path>'` when requested, the path in single quotes
   so the shell passes it as one argument; if the path itself contains a single
   quote, ask for another path instead of running the command. Do not write a context
   snapshot: voxd seeds its agent with the allowlisted repositories and the
   workers' progress itself.

2. Tell the human the call is starting, then end your turn. Do not poll.

3. **When the background command exits**, its output ends with the call's
   transcript (`user:` / `agent:` lines), one `tool:` line per worker tool the
   agent called, and a `trace:` path. Report what the agent did -- which
   workers it started, steered or stopped, and on what -- in two or three
   sentences. If the command exited with an error instead (`Call unavailable`,
   `error:`), report the error from its output.

### status

Run `vox call status` and report the state and the last few turns.

### start (the pi call)

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

   Append `--script '<path>'` when it was given, the path in single quotes
   so the shell passes it as one argument; if the path itself contains a single
   quote, ask for another path instead of running the command.

3. Tell the human the call is starting, then end your turn. Do not poll.

4. **When the background command exits**, its last line is either
   `call summary: <path>` or `no call summary: <reason>`. Read the summary
   file and report its Decisions Made and Action Items to the human. The
   Action Items are repository changes the call agent could not make itself;
   confirm with the human before carrying them out.

   If the command exited with an error instead, report the error from its
   output.

### stop

Run `vox call stop`. With a pi call live, it is a fire-and-forget signal to
the running `vox call start`, through the cross-process control file
(`src/punt_vox/voxd/conversation_mode/call_control.py`); the call then ends
cleanly and writes its summary. Otherwise it asks `voxd` to hang up its call;
the background `vox call` then exits with the transcript. Workers the call
started keep running.

## What the pi call agent is

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

While a pi call is active, `plugin/hooks/call-lock.sh` blocks the human's own
interactive prompts, self-clearing if the holding process has died.
`/call stop` passes the lock unconditionally.
