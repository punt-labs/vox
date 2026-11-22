# vox

> Voice and audio for your AI coding assistant.

[![License](https://img.shields.io/github/license/punt-labs/vox)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/punt-labs/vox/test.yml?label=CI)](https://github.com/punt-labs/vox/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/punt-vox)](https://pypi.org/project/punt-vox/)
[![Python](https://img.shields.io/pypi/pyversions/punt-vox)](https://pypi.org/project/punt-vox/)
[![Working Backwards](https://img.shields.io/badge/Working_Backwards-hypothesis-lightgrey)](./prfaq.pdf)

vox adds text-to-speech (TTS) and background music to Claude Code and to your own scripts. It speaks when a task finishes, hits an error, or needs approval, with a voice you choose and a mood (the "vibe") that sets ElevenLabs expressive tags. It works with five TTS providers and falls back to the operating system's voice (`say` on macOS, `espeak-ng` on Linux) with no setup. It can also generate instrumental background music in a genre you describe.

**Platforms:** macOS, Linux

## Diagnostics and usage telemetry

Private diagnostic logs live at `~/.punt-labs/vox/logs/vox.log` (0600 files,
0700 directories). `--verbose` and the `log_level` setting control file verbosity.
The daemon never mirrors diagnostics to stderr; clients continue without a file
sink if private logging cannot be configured, with one metadata-only warning.

Usage telemetry is local-only by default, stored separately in
`~/.punt-labs/vox/telemetry.db`, with 90-day retention. It captures versioned spans
for MCP tools, dispatched hooks, top-level CLI commands, and daemon synthesis:
closed operation/provider names, success/error outcomes, cache hits, and text
lengths. It never captures text, prompts, paths, credentials, or exception messages.
Unknown operation names become `other`. Shared `punt-observe` configuration owns
exporters; network export requires explicit opt-in consent.

Set `VOX_TELEMETRY=off` to disable vox usage telemetry, or `PUNT_TELEMETRY=off`
to disable it for all Punt tools. Either switch wins; disabled telemetry opens no
database and creates no salt. Diagnostic logging remains available.

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/vox/ce8d4a5/install.sh | sh
```

Restart Claude Code, then turn vox on for the current repository:

```text
/vox enable
```

Ask for a spoken summary of what just happened:

```text
/vox:recap
```

### CLI-Only Install (No Claude Code Plugin)

This installs the `vox` command without the Claude Code plugin, for use with another coding assistant (Codex, Cursor), a plain terminal, or a Claude Code setup where plugins are blocked. The command, the audio daemon, its directories and sample content still install; only the marketplace and plugin steps are skipped. Turn a repository on with `vox enable`.

Pass the flag:

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/vox/ce8d4a5/install.sh | sh -s -- --no-plugin
```

Or set the environment variable, for places where passing arguments is awkward (CI templates, proxies):

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/vox/ce8d4a5/install.sh | VOX_NO_PLUGIN=1 sh
```

`VOX_NO_PLUGIN` skips the plugin only when set to exactly `1`; any other value is ignored. The installer also skips the plugin when `claude` or `git` is not installed.

<details>
<summary>Manual install (if you already have uv)</summary>

```bash
uv tool install punt-vox
```

```bash
vox install
```

```bash
vox doctor
```

</details>

<details>
<summary>Verify before running</summary>

```bash
curl -fsSL https://raw.githubusercontent.com/punt-labs/vox/ce8d4a5/install.sh -o install.sh
```

```bash
shasum -a 256 install.sh
```

```bash
cat install.sh
```

```bash
sh install.sh
```

</details>

## Features

- **Spoken notifications** --- a spoken summary when a task finishes, a chime when Claude needs input
- **Session vibe** --- `/vibe` sets a mood that changes how ElevenLabs voices speak; see [Set the Vibe](#set-the-vibe)
- **Five providers** --- ElevenLabs, OpenAI, AWS Polly, macOS `say`, and Linux `espeak-ng`; see [Providers](#providers)
- **Off by default** --- no audio in a repository until you run `/vox enable` there
- **Voice or chime** --- `/mute` switches to two notification tones and makes no TTS API calls
- **Optional for Claude Code** --- Claude Code runs normally when vox is not installed
- **Claude Code plugin** --- slash commands, hooks, and an MCP server named `mic`
- **Audio daemon** --- `voxd` plays one sound at a time, drops identical requests from several sessions, and caches results
- **Background music** --- `/music on` generates up to 12 instrumental tracks with the ElevenLabs Music API; see [Background Music](#background-music)
- **Music Player (lux)** --- a clickable album list and play controls in the [lux](https://github.com/punt-labs/lux) display; see [Music Player](#music-player)
- **Control Panel (lux)** --- a lux menu entry for notification and voice settings; see [Control Panel](#control-panel)
- **Voice calls** --- `vox call start` talks with a read-only agent that can search the repository; see [Conversation Mode](#conversation-mode)

## What It Looks Like

### Hear It

Real samples generated by vox with ElevenLabs v3. The first three are the same recap with different `/vibe` moods: the expressive tags change how the voice sounds without changing the words.

| Sample | Vibe | Voice | |
|--------|------|-------|-|
| Task recap | neutral | sarah | [listen](https://github.com/punt-labs/vox/releases/download/v1.2.4/recap-neutral.mp4) |
| Same recap | `[excited]` | sarah | [listen](https://github.com/punt-labs/vox/releases/download/v1.2.4/recap-excited.mp4) |
| Same recap | `[weary] [sighs]` | sarah | [listen](https://github.com/punt-labs/vox/releases/download/v1.2.4/recap-weary.mp4) |
| Task complete | neutral | matilda | [listen](https://github.com/punt-labs/vox/releases/download/v1.2.4/task-complete.mp4) |

### Enable Notifications

```text
> /vox enable

Vox enabled. You'll hear when tasks finish or need approval.
Pick a voice with /vox:voice.
```

`/vox enable` also changes the repository's `.claude/settings.json`; see [Permissions](#permissions).

### Get a Recap

```text
> /vox:recap

Speaking: "I refactored the authentication module into three files, added
comprehensive tests for the token refresh flow, and fixed a race condition
in the session middleware. All 47 tests pass."
```

### Set the Vibe

```text
> /vibe banging my head against the wall

Vibe: banging my head against the wall → [frustrated] [sighs] [manual]
```

In auto mode (the default) the agent sets the mood from the conversation, prompted by a periodic reminder. A run of failures moves the voice toward `[frustrated]` and then `[weary]`; once work goes well again it returns to `[happy]`, through a `[relieved]` step. Expressive tags work only with ElevenLabs.

### Switch to Chime-Only

```text
> /mute

Muted — chimes only.
```

There are two chimes: one for task completion and one for a permission prompt. The vibe changes the spoken voice, not the chimes.

## Commands

### Slash Commands

| Command | What it does |
|---------|--------------|
| `/vox enable` | Turn vox on for this repository |
| `/vox disable` | Turn vox off for this repository |
| `/vox:recap` | Speak a short summary of Claude's last response |
| `/vox:voice [<name>]` | Set the session voice (no argument opens a picker) |
| `/vox:provider [<name>]` | Switch TTS provider (no argument opens a picker) |
| `/vox:model [<name>]` | Switch TTS model (no argument opens a picker) |
| `/unmute` | Speak notifications |
| `/mute` | Chimes only, no voice |
| `/vibe <mood>` | Set the session mood |
| `/vibe auto` | Let the agent set the mood from the session (default) |
| `/vibe off` | Turn the vibe off; the voice is neutral |
| `/music on` | Start generated background music in the current style |
| `/music on style techno` | Start music with a style; the style persists across calls |
| `/music on --title focus-beats` | Generate and save under a title, or replay it if it exists |
| `/music next` | Jump to another track now |
| `/music prev` | Step back one track |
| `/music pause` | Pause playback |
| `/music resume` | Resume playback |
| `/music play <name>` | Replay a saved album by name (zero credits) |
| `/music play` | Replay the last-played album |
| `/music list` | List saved albums |
| `/music status` | Show the current music state (same as `/music`) |
| `/music stop` | Stop background music |
| `/call start <topic>` | Start a voice call with a read-only call agent about `<topic>` |
| `/call stop` | Hang up |

### CLI

vox also works as a standalone TTS tool, without Claude Code.

#### Speech and Session Settings

| Command | What it does |
|---------|--------------|
| `vox say "Hello world"` | Synthesize and play text; reads stdin when piped |
| `vox say "Wall broadcast" --once 600` | Skip the play if the same text played in the last 600 seconds |
| `vox vibe excited` | Set the session mood |
| `vox notify normal` | Notify on task completion and permission prompts |
| `vox notify continuous` | Also announce real-time signals |
| `vox speak n` | Chimes only (`vox speak y` turns speech back on) |
| `vox voice` | List the voices for the active provider |
| `vox voice matilda` | Set the session voice |
| `vox provider` | List the five providers and mark the current one |
| `vox provider openai` | Set the TTS provider |
| `vox model` | List the models the current provider offers |
| `vox model v3` | Set the TTS model (shorthands: `v3`, `flash`, `turbo`, `multilingual`) |
| `vox enable` | Turn vox on for this repository |
| `vox disable` | Turn vox off for this repository |
| `vox status` | Show the daemon, voice, vibe, notification, and Claude Desktop state |

#### Recordings

| Command | What it does |
|---------|--------------|
| `vox rec new "Hello world"` | Synthesize into the recording store and print its id |
| `vox rec list` | List stored recordings |
| `vox rec play 5b9fc536a112.mp3` | Play a stored recording on the daemon's machine |
| `vox rec get 5b9fc536a112.mp3` | Copy a recording into the current directory |
| `vox rec remove 5b9fc536a112.mp3` | Delete a recording from the store |

#### Music

| Command | What it does |
|---------|--------------|
| `vox music play --style techno` | Play a style radio |
| `vox music play focus-beats` | Replay a saved album by name (zero credits) |
| `vox music play 7f3a91` | Replay a saved album by id |
| `vox music play` | Replay the last-played album |
| `cat pool.json \| vox music on --style techno` | Start music from a 12-track pool you write (`{base_prompt, variations}`) |
| `vox music new "warm analog pads, D minor" --title night-pads` | Generate one track from your exact prompt into a new album |
| `vox music next` | Jump to another track |
| `vox music prev` | Step back one track |
| `vox music pause` | Pause playback |
| `vox music resume` | Resume playback |
| `vox music status` | Show what is playing |
| `vox music list` | List saved albums |
| `vox music get 7f3a91` | Copy an album into the current directory |
| `vox music get 7f3a91 --dest ~/Music` | Copy an album into a directory you name |
| `vox music remove 7f3a91` | Delete an album (a playing album is refused) |
| `vox music stop` | Stop background music |

#### Voice Calls

| Command | What it does |
|---------|--------------|
| `vox call start --context snapshot.md` | Start a voice call; the call agent opens with the snapshot file |
| `vox call start --context - < snapshot.md` | Read the snapshot from stdin |
| `vox call start --context snapshot.md --trace-turns` | Also print a turn-by-turn latency trace |
| `vox call stop` | Hang up |
| `vox call context --session <id>` | Inspect curated Claude Code hook context and sequence gaps |

#### Installation and the Daemon

| Command | What it does |
|---------|--------------|
| `vox install` | Install the Claude Code plugin and the daemon service |
| `vox uninstall` | Remove the Claude Code plugin |
| `vox doctor` | Check the setup |
| `vox version` | Print the version |
| `vox daemon install` | Register `voxd` as a service, seed `keys.env`, and start it |
| `vox daemon uninstall` | Remove the `voxd` service |
| `vox daemon start` | Start `voxd` and confirm it runs the installed version |
| `vox daemon stop` | Stop `voxd` and confirm it exited |
| `vox daemon restart` | Stop, then start |
| `vox daemon status` | Report `running`, `unresponsive`, `unknown`, or `not_running` (exit code 0 only when running) |
| `vox daemon status --json` | Report the daemon's health as JSON |
| `vox log debug` | Set the daemon's log level (`info` or `debug`) |
| `vox cache status` | Show the synthesis cache's entry count, size, and path |
| `vox cache clear` | Delete all cached MP3 files |
| `vox desktop install` | Register the MCP server with Claude Desktop |
| `vox desktop uninstall` | Remove the Claude Desktop registration |
| `vox mcp` | Run the MCP server over stdio |
| `voxd` | Run the audio daemon in the foreground |

## API

vox is also a Python library. It talks to the same `voxd` daemon as the plugin and the CLI; `vox install` is the only setup.

```python
from punt_vox import VoxClientSync, SynthesisSpec, VoxError

vox = VoxClientSync()  # connects per call — no setup, no teardown

try:
    # Speak text through the daemon (plays on the local audio device):
    vox.synthesize("Build finished", SynthesisSpec(voice="sarah"))

    # ...or get the MP3 bytes to save or stream yourself:
    audio = vox.record("Deploy complete", SynthesisSpec(voice="matilda"))

    # List the voices the active provider offers:
    print(vox.voices())
except VoxError as exc:  # VoxdConnectionError, VoxdProtocolError, or VoxdRejectionError
    print(f"vox failed: {exc}")
```

`VoxClient` is the async version. It connects once and is an async context manager:

```python
import asyncio
from punt_vox import VoxClient, SynthesisSpec


async def main() -> None:
    async with VoxClient() as vox:  # connect on enter, close on exit
        await vox.synthesize("Tests green", SynthesisSpec(voice="sarah"))
        health = await vox.health()  # typed HealthStatus, not a bare dict
        print(health.provider, health.daemon_version)


asyncio.run(main())
```

Every failure raises a `VoxError` subclass, and `health()` and the `program_*` methods return typed results (`HealthStatus`, `ProgramStatus`, …) rather than dicts. The public names are listed in `punt_vox.__all__`: `VoxClient`, `VoxClientSync`, `SynthesisSpec`, `SynthesizeResult`, `VoxError`, `VoxdConnectionError`, `VoxdProtocolError`, `VoxdRejectionError`, `HealthStatus`, `ProgramStatus`, `CommandOutcome`, `ProgramSummary`, and `PromptSet`. The package ships type information (`py.typed`). Importing `punt_vox` does not import any provider SDK.

## Setup

### Configure Providers

The Quick Start uses the operating system's built-in voice: `say` on macOS, `espeak-ng` on Linux. For a more natural voice, configure a cloud provider. The `/vibe` expressive tags (`[excited]`, `[weary]`, `[sighs]`, and so on) need ElevenLabs; no other provider supports them.

#### 1. Get an API Key

| Provider | Where to sign up | Free tier |
|---|---|---|
| **ElevenLabs** (recommended) | [elevenlabs.io](https://elevenlabs.io/sign-up) → [Settings → API Keys](https://elevenlabs.io/app/settings/api-keys) | 10k characters/month |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) → [API Keys](https://platform.openai.com/api-keys) | None — pay-as-you-go |
| **AWS Polly** | Any AWS account; create an IAM user with the `AmazonPollyReadOnlyAccess` policy | 5M chars/month, first 12 months |

#### 2. Add the Keys to `~/.punt-labs/vox/keys.env`

The keys file is in your home directory and owned by you, so you can edit it without `sudo`. Open it in any editor:

```bash
nano ~/.punt-labs/vox/keys.env
```

Add the lines that apply. All are optional; vox detects which providers are configured.

```ini
# ElevenLabs — recommended
ELEVENLABS_API_KEY=sk_...

# OpenAI
OPENAI_API_KEY=sk-proj-...

# AWS Polly via an aws CLI profile (recommended if you use the AWS CLI)
AWS_PROFILE=default
AWS_DEFAULT_REGION=us-east-1

# AWS Polly via raw credentials (alternative to a profile)
# AWS_ACCESS_KEY_ID=AKIA...
# AWS_SECRET_ACCESS_KEY=...
# AWS_DEFAULT_REGION=us-east-1

# Optional: pin a specific provider.
# Auto-detect order: ElevenLabs > OpenAI > Polly > say (macOS) / espeak (Linux).
TTS_PROVIDER=elevenlabs
```

`vox daemon install` also copies into `keys.env` any provider keys set in the shell that runs the install, so keys set in `.envrc` or similar before installing work too. After install, edit the file directly.

#### 3. Restart the Daemon

```bash
vox daemon restart
```

Run it as your normal user, **not** under `sudo`. On **macOS** it needs no password, because `voxd` is a user LaunchAgent. On **Linux** it asks for your `sudo` password once, only for the `systemctl` call that manages the system service.

#### 4. Verify

Report system checks and per-provider readiness:

```bash
vox doctor
```

List the voices your provider offers:

```bash
vox voice
```

Speak through the default provider:

```bash
vox say "hello from vox"
```

`vox doctor` reports the Python version, whether ffmpeg and espeak are present, the daemon status, and which providers the daemon has credentials for and which one it prefers. It checks the *daemon's* environment, not your shell's: an `ELEVENLABS_API_KEY` exported in your terminal does not change the report. Edit `keys.env` and run `vox daemon restart` to change what the daemon sees.

If something does not work, check the daemon log at `~/.punt-labs/vox/logs/vox.log`. It records the player command, the audio session environment, the exit code, the elapsed time, and the player's error output.

### Providers

ElevenLabs is the only provider with expressive tags, so `/vibe` changes the voice only with ElevenLabs. The other providers work without it.

| Provider | API Key | Default Voice | Notes |
|----------|---------|---------------|-------|
| **ElevenLabs** | `ELEVENLABS_API_KEY` | matilda | **Recommended.** Expressive tags via `/vibe` |
| OpenAI | `OPENAI_API_KEY` | nova | |
| AWS Polly | AWS credentials | joanna | |
| macOS say | — | samantha | No setup on macOS, works offline |
| espeak-ng | — | en | No setup on Linux, works offline |

Auto-detection order: ElevenLabs > OpenAI > Polly (if AWS credentials are valid) > say (macOS) / espeak (Linux).

### Per-Call API Keys for Billing Isolation

If you keep several provider API keys to track cost (for example, a separate ElevenLabs key per project), you can pass a key for a single `vox say` call. The key is used for that call only: it is not saved to `keys.env`, not logged by the daemon, not printed, and not visible to other requests on the same daemon. There are four ways to pass it, from most to least secure.

1. **Environment variable** (recommended for scripts). Set `VOX_API_KEY`, for example from a password manager:

   ```bash
   export VOX_API_KEY=$(pass show vox/proj_a)
   ```

   Then call `vox say` as usual:

   ```bash
   vox say "billable to project A"
   ```

   On Linux, a process's environment is visible in `/proc/<pid>/environ`, which normally only the process owner can read. macOS has no `/proc`. In both cases an environment variable is less exposed than a command-line argument, which `ps` shows to every user on the machine.

2. **File** (recommended for stored keys):

   ```bash
   vox say "billable to project A" --api-key-file ~/.config/vox/key_project_a.txt
   ```

   The file should be mode 0600 (owner read/write only). `vox` warns and suggests `chmod 600` if group or other permission bits are set.

3. **Standard input** (recommended for password managers):

   ```bash
   pass show vox/proj_a | vox say "billable to project A" --api-key-stdin
   ```

   This reads one line from stdin. It refuses to read from a terminal, so a forgotten pipe fails at once instead of waiting for input.

4. **Command line** (for demos only, **not** for real keys):

   ```bash
   vox say "billable to project A" --api-key sk_demo_key
   ```

   **Warning:** a key on the command line is visible to `ps` (and on Linux in `/proc/*/cmdline`), in shell history, and in terminal recordings. `vox` prints a warning to stderr whenever you use `--api-key`.

You can use only one of the four at a time; passing more than one is an error. This is **not** isolation between users: vox runs for one user, and the feature attributes spending to projects within that user's account.

Calls with a per-call key skip the synthesis cache, so every call reaches the provider. Calls that use `keys.env` can be served from the cache.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TTS_PROVIDER` | Force a specific provider | auto-detect |
| `TTS_MODEL` | Model override | provider default |
| `VOX_OUTPUT_DIR` | Output directory | `~/Music/vox/` |
| `VOX_API_KEY` | Per-call provider key for `vox say` | none |
| `VOXD_HOST` | Remote voxd host (client) | `127.0.0.1` |
| `VOXD_PORT` | Remote voxd port (client) | read from the `serve.port` file |
| `VOXD_TOKEN` | Remote voxd auth token (client) | read from the `serve.token` file |
| `VOXD_BIND` | voxd bind address (server); must be an IP address, not a hostname | `127.0.0.1` |

Provider API keys (`ELEVENLABS_API_KEY`, `OPENAI_API_KEY`, `AWS_*`) belong in `~/.punt-labs/vox/keys.env`, not in your shell startup file. See [Configure Providers](#configure-providers).

### Permissions

`vox enable` adds these rules to the repository's `.claude/settings.json`, so Claude Code does not ask before using vox's own tools in that repository:

- **Allowed:** `Bash(vox:*)`, `mcp__plugin_vox_mic__*`, and `Skill(vox:*)`.
- **Still asks first:** the `vox` subcommands `install`, `uninstall`, `enable`, `disable`, `daemon`, `desktop`, `cache`, and `mcp`. A `vox` command that starts with an option, such as `vox --version`, does not ask.

`vox disable` removes exactly those rules. Both commands also remove `Bash(vox -*)`, which earlier versions added. Neither command changes anything outside the repository. Rules that older versions of vox added to `~/.claude/settings.json` stay until you delete them.

### Upgrading

`uv tool upgrade punt-vox` replaces the installed package, but it does **not** restart the running `voxd` daemon, which keeps running the old version. Upgrade, then restart the daemon:

```bash
uv tool upgrade punt-vox
```

```bash
vox daemon restart
```

Run `vox daemon restart` as your normal user; it refuses to run as root. On Linux it runs `sudo -n systemctl …` and asks for your password once, only when a terminal is attached. `restart` is `vox daemon stop` followed by `vox daemon start`. `stop` succeeds only when the service is inactive, nothing holds port 8421, and every `voxd` process it found has exited. `start` succeeds only when the service's process answers the authenticated health check with the installed version. Each run prints one outcome (`stopped`, `started`, `restarted`, `already running`, `timeout`, …) and names any process it had to signal; use `--json` for scripts. On failure it points you to `~/.punt-labs/vox/logs/vox.log`.

To confirm the daemon runs the installed version:

```bash
vox doctor
```

`vox doctor` reports the running daemon's version. When it differs from the installed package, doctor prints a yellow warning (`⚠ Daemon: running ... (version X — wheel has Y, run 'vox daemon restart' to refresh)`) and still exits 0.

### Service Install

```bash
vox daemon install
```

This registers `voxd` as a service, creates `keys.env`, and starts the daemon. On **macOS** `voxd` is a user LaunchAgent, so installing, restarting, and removing it need no password. On **Linux** `vox` asks for your `sudo` password once to place the systemd unit file; everything else runs as your normal user. The daemon runs as the installing user, not as root, because it needs the audio devices of your desktop session.

### State Paths

`voxd` runs as one user, so its state is per user. All vox state lives under `~/.punt-labs/vox/`, with the same layout on macOS and Linux. The one file outside your home directory is the Linux systemd unit, in `/etc/systemd/system/`.

| Purpose | Path |
|---------|------|
| Config (API keys) | `~/.punt-labs/vox/keys.env` |
| Agent usage guide (per enabled repo) | `.punt-labs/vox/CLAUDE.md` |
| Logs | `~/.punt-labs/vox/logs/vox.log` |
| Runtime state | `~/.punt-labs/vox/run/serve.{port,token}` |
| Cache | `~/.punt-labs/vox/cache/` |
| Call summaries | `~/.punt-labs/vox/calls/` |
| Service unit (Linux) | `/etc/systemd/system/voxd.service` |
| Service plist (macOS) | `~/Library/LaunchAgents/com.punt-labs.voxd.plist` |

Per-repository settings live in `.punt-labs/vox/` in each repository, in two files: `vox.md` holds settings meant to be committed (voice, provider, model), and `vox.local.md` is git-ignored and holds session state such as the current vibe.

### Agent Usage Guide

`vox enable` is the only guide writer. It deposits `.punt-labs/vox/CLAUDE.md` in the repo and adds `@.punt-labs/vox/CLAUDE.md` to the repo's `CLAUDE.md`, so the guide loads only in enabled projects. `vox install`, `vox uninstall`, and `install.sh` touch no `CLAUDE.md`. A stale `@~/.punt-labs/vox/CLAUDE.md` import in `~/.claude/CLAUDE.md` and the old `~/.punt-labs/vox/CLAUDE.md` guide are left alone; delete both by hand.

## Ways to Use vox

All three ways use the same `voxd` audio daemon, so vox also works outside Claude Code:

- **Claude Code plugin** --- the `mic` MCP server gives the agent voice, `/vibe`, and `/music` tools, plus notification hooks. See [Slash Commands](#slash-commands).
- **Python library** --- `from punt_vox import VoxClient`. See [API](#api).
- **CLI** --- `vox say "Build finished"`, `vox rec new "..."`, `vox music play`. See [CLI](#cli).

## Remote Audio

You can run Claude Code on a headless server or cloud VM and hear the audio on your own machine. Set `VOXD_HOST`, `VOXD_PORT`, and `VOXD_TOKEN` on the remote host to point it at the `voxd` on your machine. See [docs/guide-remote-setup.md](docs/guide-remote-setup.md) for direct-network and SSH-tunnel setups.

## Background Music

`/music on` plays generated instrumental music. It plays the first track as soon as it is ready, then generates the rest of the pool (up to 12 distinct tracks) in the background and moves to a different track as each one ends. When the pool holds 12 tracks, generation stops and playback rotates through the pool in shuffled order, never repeating a track twice in a row, at **zero credits**.

vox passes prompts to the ElevenLabs Music API; it does not decide what a genre sounds like. On `/music on` the agent writes a base prompt for the genre plus 12 variations, so the pool holds 12 different tracks in one style.

Each album is saved under `~/Music/vox/<name>/`, named by `--title` or else by the style, with ID3 tags (artist, album, title, genre, track number) on every track, so the music shows up in macOS Music.app, Rhythmbox, and other players. Replaying a saved album or rotating a full pool costs nothing; generation runs only while a pool has fewer than 12 tracks.

**Requires an ElevenLabs paid plan.** Each generated track costs about 2,000 credits (about 3 minutes of audio), so a full 12-track pool costs about **24,000 credits**.

## Music Player

When [lux](https://github.com/punt-labs/lux) is running on the same machine, `voxd` adds a **voxd ▸ Music** menu entry to the lux display. It shows your saved albums as a list, each with a Play control, above play/pause, previous, next, and stop controls. Clicking an album plays it through your speakers, and the display shows what is playing and the track position (read from the album's ID3 tags). If the lux display restarts, `voxd` reconnects and adds the menu again. Without lux, the rest of vox works the same; only the player needs it.

## Control Panel

When vox is enabled in a repository and lux is running, each Claude Code session adds a `Vox` entry to the lux menu. It opens a settings panel for chimes versus voice, the notification level (off, normal, continuous), and the voice, with a preview button. The panel changes the same settings as the CLI and slash commands, so they always agree. If a change cannot be saved or `voxd` is unreachable, the panel says so in a status line.

## Conversation Mode

`vox call start` starts a voice call with a read-only call agent. vox listens through the microphone, transcribes each turn with ElevenLabs, sends it to the agent, and speaks the reply. The agent is a `pi --mode rpc` process that runs for the whole call. It does not see your Claude Code session: it starts from a context snapshot your session writes, and it can only read and search files in the repository.

Separately, the Vox plugin forwards live Claude Code hooks from enabled
projects to voxd. Use `vox call context --session <id>` to inspect a session's
goal, active files, recent tool results, open failure, and last assistant
message without placing a call. Credential-shaped keys are redacted at
every depth; this does not scrub secrets embedded in free-form text.
Context is bounded and in memory only. Sequence gaps can close as late hooks
arrive; drops after the last received hook are not visible yet. Gaps measure
what the current snapshot has not seen, including history lost on a daemon
restart or session eviction; they do not prove a transport drop. If older
missing ranges expire, `untracked_through` marks that part of the history.
Older arrivals in that expired portion are refused because the daemon can
no longer distinguish them from duplicates.
Small sender counter files persist so resumed sessions keep their
sequence numbers. They do not contain hook payloads.

In Claude Code, `/call start <topic>` writes the snapshot from the topic and the current work, then starts the call. `/call stop` hangs up. While a call is active, your own prompts to Claude Code are blocked until you run `/call stop` (or `/vox:call stop`), which still works. When a call ends normally, the agent writes a summary (decisions, action items, context gathered, open questions) to `~/.punt-labs/vox/calls/`, and `/call start` reads it back into your session. The newest 50 summaries are kept.

**Requirements.** The call agent needs `pi` on `PATH`, signed in through its own config (`~/.pi/agent`), plus `git` and ripgrep (`rg`); `vox call start` stops with an install hint if `rg` is missing. A TTS provider must be configured to speak the replies. Microphone capture needs the `call` extra, which installs `sounddevice`. If you used the installer or `uv tool install`, reinstall with the extra:

```bash
uv tool install --force "punt-vox[call]"
```

If you installed with pip:

```bash
pip install "punt-vox[call]"
```

`sounddevice` uses PortAudio, a system library that pip and uv cannot install. On macOS, run `brew install portaudio`; on most Linux distributions, install `libportaudio2` (for example `apt install libportaudio2`). Other `vox` commands work without the extra or PortAudio.

**Limits on the agent.** It runs with only the `read`, `grep`, `find`, and `ls` tools; no extensions, skills, MCP servers, or repository context files; and an environment reduced to `PATH`, `HOME`, and locale and terminal variables, with no API keys or tokens from your shell. vox checks every tool call before it runs and refuses any path outside the repository, inside `.git/`, matching a secret pattern (`.env*`, keys, credential files, `.git-credentials`), or ignored by git (every `.gitignore`, `.git/info/exclude`, and your global excludes, as reported by git). A directory search is refused if it would read any refused file. Text that looks like a secret is removed from the spoken reply and the saved summary.

## Architecture

```text
Claude Code ◄── stdio ──► vox mcp ── WebSocket ──► voxd :8421
                                                      │
Hook scripts ──► vox hook <event> ── WebSocket ──►    │
                                                      │
Shell        ──► vox say "hi"  ── WebSocket ──►    │
                                                      ▼
                                                   speakers
```

**`voxd`** is the audio daemon. It synthesizes text through the TTS providers and plays audio through the speakers. It plays one sound at a time, drops a request identical to one received in the last 5 seconds, and caches synthesis results. It has no knowledge of MCP, hooks, projects, or Claude Code.

**`vox mcp`** is the MCP server, one per Claude Code session. It keeps the session's voice, provider, vibe, and notification mode in memory, and sends synthesis requests to `voxd`. The daemon keeps no session state and never reads the repository's vox files. It finds the repository's `.punt-labs/vox/` directory by searching upward from Claude Code's working directory.

**`vox hook <event>`** runs on Claude Code events and asks `voxd` to play chimes and speech.

**`vox say`** and the other CLI commands each open a short connection to `voxd`.

The connection between Claude Code and `vox mcp` does not depend on the daemon, and `vox mcp` reconnects to `voxd` on its own, so restarting the daemon does not lose session settings.

## Related Approaches

- **`say` / `espeak-ng` in a shell hook** --- One line of shell on macOS or Linux. Sequential playback is left to the caller; overlapping calls compete for the audio device. No deduplication across sessions and no voices beyond what the OS ships. vox uses these same programs as its offline fallback.
- **Editor-side TTS plugins** (VS Code speech, Neovim `:!say`, ...) --- Tied to one editor. Claude Code runs in the terminal.
- **`notify-send` / macOS `terminal-notifier`** --- Visual notifications, not audio.

## Documentation

[Architecture (PDF)](docs/architecture.pdf) |
[Remote Setup](docs/guide-remote-setup.md) |
[Design Log](DESIGN.md) |
[Testing](TESTING.md) |
[Contributing](CONTRIBUTING.md) |
[Changelog](CHANGELOG.md)

## License

MIT
