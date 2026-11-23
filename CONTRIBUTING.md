# Contributing to vox

The full development workflow is in [docs/WORKFLOW.md](docs/WORKFLOW.md), and the testing tiers are in [TESTING.md](TESTING.md). This page lists the setup and the quality-gate commands.

## Setup

vox needs Python 3.13+ and [uv](https://docs.astral.sh/uv/). `make lint` also needs ShellCheck, and `make docs` needs Node.js (for `npx`).

Install the development tools:

```bash
uv sync
```

Run `make hooks` once after cloning to install the local commit and push gates.

Also install the optional `call` extra for live microphone capture in `vox call`:

```bash
uv sync --extra call
```

## Quality Gates

Each commit must pass the commit hook's checks. Run the full gate at handoff
or before opening a PR. It must finish with zero violations and all tests passing:

```bash
make check
```

`make check` runs these targets, which you can also run one at a time.

Lint (Ruff, ShellCheck, and the plugin-surface check):

```bash
make lint
```

Type checks (mypy and pyright):

```bash
make type
```

Markdown lint:

```bash
make docs
```

Default test tier:

```bash
make test
```

OO, coupling, and suppression ratchets:

```bash
make check-oo
```

```bash
make check-coupling
```

```bash
make check-suppressions
```

CI measures all credential-free tiers, including slow, subprocess, and installed
wheel e2e, with combined line and branch coverage:

```bash
make coverage
```

On PRs, neither total line nor branch coverage may decrease versus the merge
base; there is no absolute percentage gate or numeric baseline to update.
See [Coverage policy](TESTING.md#coverage-policy) for reports and local comparison.

CI also runs installed-wheel packaging smoke in a separate `packaging-e2e`
job. To run it locally:

```bash
make install
make test-e2e
```

This `e2e` marker checks CLI/MCP packaging, not manual live-audio quality.

List all Make targets:

```bash
make help
```

## Testing a Call Without a Microphone

`vox call --script` streams recorded turns from a JSONL file to the
ElevenLabs voice agent instead of using the microphone. See
[Vox call](README.md#vox-call) for credentials and the script format.

```bash
vox call --script turns.jsonl
```

## Plugin Layout

Everything the Claude Code plugin ships is under `plugin/`: `plugin/.claude-plugin/plugin.json`, `plugin/commands/`, and `plugin/hooks/`. The marketplace installs only that directory, so an installed plugin has no `src/`, `tests/`, `docs/`, or `scripts/`. Two rules follow:

- A hook script may use `$HOME`, the `vox` binary, and paths under the consumer's repository root, but no other path in this repository.
- Load the plugin for development with `claude --plugin-dir plugin`, not `claude --plugin-dir .`, so `CLAUDE_PLUGIN_ROOT` is the same directory the marketplace installs.

On `main`, the plugin is named `vox-dev`. `vox enable` allows only the `mcp__plugin_vox_mic__*` tools, so maintainers running `vox-dev` add `mcp__plugin_vox-dev_mic__*` to their permissions by hand.

Hook scripts follow the [hooks standard](https://github.com/punt-labs/punt-kit/blob/main/standards/hooks.md): the shell script is a thin gate, and `vox hook <event>` does the work.
