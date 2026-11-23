---
description: "Talk to Vox and delegate coding to persistent workers"
argument-hint: "--headset | --aec | --script <path> | status | stop"
allowed-tools: ["Bash(vox call:*)"]
---

# /call command

Talk to the ElevenLabs voice agent in voxd. It can start, steer, check and
stop persistent coding workers in allowed repositories. Workers keep running
after you hang up and use their harness's existing permissions.

## Usage

- `/call --headset` -- talk to voxd's call agent while wearing a headset.
- `/call --aec` -- use a system echo-cancelled microphone and speaker path
  you have already configured. Vox does not set up echo cancellation.
- `/call --script <path>` -- dev/test path: scripted turns from a JSON Lines
  file of recorded speech (`{"pcm_path": ..., "pause_s": ...}` per line: 16 kHz
  mono S16LE PCM, a relative path read from the script's directory) instead of
  the microphone. For demos and CI.
- `/call status` -- show voxd's call: its state, transcript and tool calls.
- `/call stop` -- ask the running call to hang up.

## Implementation

### Place a call

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
   quote, ask for another path instead of running the command. Do not use
   unrecognized arguments as shell commands or as call input.

2. Tell the human the call is starting, then end your turn. Do not poll.

3. **When the background command exits**, its output ends with the call's
   transcript (`user:` / `agent:` lines), one `tool:` line per worker tool the
   agent called, and a `trace:` path. Report what the agent did -- which
   workers it started, steered or stopped, and on what -- in two or three
   sentences. If the command exited with an error instead (`Call unavailable`,
   `error:`), report the error from its output.

### status

Run `vox call status` and report the state and the last few turns.

### stop

Run `vox call stop`. The background call exits with its transcript; workers
keep running.
