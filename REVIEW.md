# REVIEW.md

Instructions for code reviewers of this repository: Qodo, Devin Review,
Copilot, and the local review agents (`code-reviewer`,
`silent-failure-hunter`, `invariant-completeness-reviewer`). Read this before
raising a finding.

vox is text-to-speech for coding agents: a `voxd` audio daemon, an MCP server,
a CLI, and a Claude Code plugin of bash hooks. Its job is to make sound at the
right moment and to tell the operator plainly when it cannot. A review that
delays a merge without making the audio or the reading more correct works
against the product.

## What we want flagged

- Logic errors a real caller of the changed code can reach.
- Audio that is lost, cut off, played twice, played with the wrong voice or
  provider, or played at the wrong moment; a chime or speech that a hook
  should fire and does not.
- A reading that lies: `vox daemon status`, `vox doctor`, `mic:status` or a
  tool result that reports a state the daemon is not in, or that hides a
  failure the operator needs.
- Secrets: a provider key or the daemon token written to a log, a URL, a
  prompt, a file with loose modes, or sent to a listener that is not this
  user's voxd.
- Daemon lifecycle defects: a restart that interrupts playback it could have
  waited for, a stale `serve.port` or `serve.pid` the reader trusts, an
  install or uninstall that leaves the unit in a state the status cannot
  describe.
- Errors swallowed or logged without the operator or the model finding out
  through the API.
- Resource leaks: audio streams, subprocesses, sockets, asyncio tasks,
  temporary files.
- Changed behavior with no test, or a test that cannot fail.
- Security defects in what vox already does: shell or argument injection in
  the hooks, paths built from outside input that escape their directory,
  unsafe deserialization, unsafe file modes.

## Out of scope

- Anything CI enforces: ruff format and lint, mypy and pyright strict,
  shellcheck, markdownlint, and the OO, coupling and suppression ratchets.
- Lines the pull request did not change.
- Hypothetical environments: exotic environment variables, control bytes in
  paths, tests run as root, platforms other than macOS and Ubuntu 24.04.
- `__new__` as the constructor. It is the standard here.
- Suppressions (`# noqa`, `# type: ignore`, `xfail`). Never suggest them.
- PR titles, descriptions, commit messages and CHANGELOG wording.
- Agent process rules. `CLAUDE.md`, `docs/WORKFLOW.md`, `docs/WORKFLOW-PR-LOOP.md`, `.punt-labs/**` and
  the mission records describe how agents work this repository: missions,
  review rounds, delegation, issue tracking, demo and merge gates. They are
  not code. Do not grade a diff against them, do not grade their pseudocode
  as if it ran, and do not ask them to specify mechanisms they do not claim.
  A finding on one of these files is in scope in two cases only: the text
  states something false about the code it describes (a command, a file, a
  module or a state that does not exist or does not behave as written); or
  the change is a security defect in an instruction an agent follows (it
  tells an agent to run a destructive or unconfirmed command, interpolates
  caller-controlled text into a command an agent runs, or weakens a gate
  that keeps a secret or the daemon token where it belongs). Security stays
  in scope in every file.
- Working-tree noise. `.punt-labs/vox/vox.md` and `.claude/settings.json`
  are tracked files the daemon and `vox enable` rewrite, so a local review
  often sees them changed when the PR did not touch them; a change to them
  that is not in the PR's commits is never the change under review. A
  committed change to them is in scope like any other. (`vox.local.md` is
  gitignored and never appears in a diff.)
- Work tracked elsewhere. A limit this repository has recorded as a Linear
  issue, and names in the diff by its PL number, is known; do not raise it
  again. This never covers a security finding: raise those whatever the diff
  cites.
- Usability-breaking "security" refusals. Normal user workflows (`vox enable`,
  `vox disable`, `--refresh`, startup, and personal-scope `.git/info/exclude`
  auto-setup) must work without extra user approval or manual setup; do not ask
  to make normal commands refuse to run.

## Docs-only pull requests

A pull request that changes only `*.md` or `docs/` is reviewed for three
things: text that is false about the code, two passages of the same file that
contradict each other, and a rule removed without saying what replaces it. Do
not propose additional mechanisms, edge-case handling or process steps; the
repository decides its process, the review checks that it is stated
consistently. This limits the kind of finding, not the number of rounds: the
remote rounds follow `docs/WORKFLOW.md` (round two only after a High fix).

A pull request that changes only `tests/` is not docs-only. It gets the test
rules above: an assertion made vacuous, a regression test deleted, or a test
that can no longer fail is in scope.

## Severity

- Important: lost, cut-off or wrong audio; a lying reading; a secret or the
  daemon token reaching the wrong place; a crash; a leak; the in-scope
  security defects above.
- Nit: everything else. At most five per pull request; group similar
  findings into one summary.
