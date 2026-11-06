"""Tests for the pi-side secret guard extension, run under real node.

``pi_extension/secret_guard.js`` is the layer that blocks a denied read before
it executes. These tests import it into a node process and drive its
``tool_call`` handler and its ``createGuard`` checker directly, against a real
temporary repository on disk (symlinks included), with the same cases the
Python :class:`SecretPathPolicy` is held to.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.call_agent_launch import (
    SECRET_GUARD,
    CallAgentLaunch,
)
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy

pytestmark = pytest.mark.subprocess

# Imports the guard, then answers each [tool, input, cwd] case with the
# reason the guard gives ("" when allowed), and drives the default export's
# tool_call handler once with the first case -- or reports the error the
# default export threw while loading.
_HARNESS = """
const [guardUrl, casesJson] = process.argv.slice(1);
const mod = await import(guardUrl);
const cases = JSON.parse(casesJson);
let reasons = null;
try {
  const guard = mod.createGuard({
    root: process.env.VOX_CALL_GUARD_ROOT,
    patterns: JSON.parse(process.env.VOX_CALL_GUARD_PATTERNS),
    gitIgnores: JSON.parse(process.env.VOX_CALL_GUARD_GIT_IGNORES ?? "null"),
  });
  reasons = cases.map(([tool, input, cwd]) => guard(tool, input, cwd));
} catch {
  // A misconfigured guard: only the handler's fail-closed answer is checked.
}
let handler;
try {
  mod.default({ on: (event, fn) => { if (event === "tool_call") handler = fn; } });
} catch (error) {
  console.log(JSON.stringify({ reasons, loadError: error.message }));
  process.exit(0);
}
const [tool, input, cwd] = cases[0];
const decision = handler({ toolName: tool, input }, { cwd }) ?? null;
console.log(JSON.stringify({ reasons, decision }));
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo whose secrets are all gitignored, as pi's grep would see it.

    pi's grep runs rg without ``--no-require-git``, so ``.gitignore`` only
    hides files inside a git repository -- hence the ``git init``.
    """
    root = tmp_path / "repo"
    (root / ".punt-labs" / "ethos").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main(): ...\n")
    (root / ".gitignore").write_text(
        ".punt-labs/**/*.local.*\n.envrc.local\n.env.local\n"
    )
    (root / ".env.local").write_text("FAKE_API_KEY=not-real\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the repo")
    (root / "innocent.txt").symlink_to(outside)
    return root


def _guard(
    repo: Path,
    cases: list[tuple[str, dict[str, str], str]],
    env: dict[str, str],
    *,
    path_var: str | None = None,
) -> dict[str, object]:
    """Run the harness; *path_var* replaces ``PATH`` for the guard's own lookups."""
    node = shutil.which("node")
    assert node is not None, "node is required: pi itself runs on it"
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            _HARNESS,
            SECRET_GUARD.as_uri(),
            json.dumps(cases),
        ],
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": os.environ["PATH"] if path_var is None else path_var,
            "HOME": os.environ["HOME"],
            **env,
        },
        timeout=30,
    )
    parsed: dict[str, object] = json.loads(result.stdout)
    return parsed


def _reasons(repo: Path, cases: list[tuple[str, dict[str, str], str]]) -> list[str]:
    env = SecretPathPolicy.for_repo(repo).guard_env()
    reasons = _guard(repo, cases, env)["reasons"]
    assert isinstance(reasons, list)
    return [str(reason) for reason in reasons]


@pytest.mark.parametrize(
    "path",
    [
        ".env.local",
        "@.env.local",
        ".ENV",
        "KEYS.ENV",
        "config/Keys.Env",
        "ID_RSA",
        "~/.SSH/Id_Ed25519.pub",
        "Credentials.JSON",
        "/proc/self/environ",
        "~/.claude/.credentials.json",
        "~/.config/gh/hosts.yml",
        "innocent.txt",
        "../outside.txt",
        "src/../../outside.txt",
        "file:///etc/passwd",
        ".punt-labs/ethos/claude.local.yaml",
        "src/credentials.json",
    ],
)
def test_the_guard_blocks_a_denied_read(repo: Path, path: str) -> None:
    (reason,) = _reasons(repo, [("read", {"path": path}, str(repo))])
    assert reason.startswith("vox secret guard:")


def test_the_guard_judges_a_relative_path_from_the_session_cwd(repo: Path) -> None:
    cwd = str(repo / ".punt-labs" / "ethos")
    (reason,) = _reasons(repo, [("read", {"path": "x.local.yaml"}, cwd)])
    assert reason


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("grep", {"pattern": "KEY", "glob": "*.env*"}),
        ("grep", {"pattern": "KEY", "path": "/etc"}),
        ("find", {"pattern": "**/*.pem"}),
        ("ls", {"path": "~"}),
        ("bash", {"command": "cat .env.local"}),
    ],
)
def test_the_guard_blocks_denied_searches_and_unknown_tools(
    repo: Path, tool: str, args: dict[str, str]
) -> None:
    (reason,) = _reasons(repo, [(tool, args, str(repo))])
    assert reason


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read", {"path": ".gitignore"}),
        ("read", {"path": "src/credentials.py"}),
        ("grep", {"pattern": "def", "path": "src"}),
        ("grep", {"pattern": "def"}),
        ("find", {"pattern": "*.py"}),
        ("ls", {}),
    ],
)
def test_the_guard_allows_ordinary_repo_reads(
    repo: Path, tool: str, args: dict[str, str]
) -> None:
    assert _reasons(repo, [(tool, args, str(repo))]) == [""]


class TestDirectoryGrep:
    """A grep over a directory reads every file rg lists there, so each one
    is judged before the grep may run."""

    def test_a_non_ignored_envrc_in_the_searched_directory_blocks_the_grep(
        self, repo: Path
    ) -> None:
        (repo / "src" / ".envrc").write_text("export TOKEN=sk-not-real\n")
        (reason,) = _reasons(repo, [("grep", {"pattern": "TOKEN"}, str(repo))])
        assert ".envrc" in reason

    def test_a_negated_glob_is_judged_by_what_it_widens_to(self, repo: Path) -> None:
        (repo / ".envrc").write_text("export TOKEN=sk-not-real\n")
        args = {"pattern": "TOKEN", "glob": "!*.py"}
        (reason,) = _reasons(repo, [("grep", args, str(repo))])
        assert reason

    def test_a_glob_that_excludes_the_secret_lets_the_grep_through(
        self, repo: Path
    ) -> None:
        (repo / ".envrc").write_text("export TOKEN=sk-not-real\n")
        args = {"pattern": "def", "glob": "*.py"}
        assert _reasons(repo, [("grep", args, str(repo))]) == [""]

    def test_gitignored_secrets_do_not_block_a_grep(self, repo: Path) -> None:
        """rg skips them, exactly as pi's grep will."""
        assert _reasons(repo, [("grep", {"pattern": "def"}, str(repo))]) == [""]

    @pytest.mark.parametrize(
        ("pattern", "secret"),
        [
            ("config/*.secret.json", "config/a\nb.secret.json"),
            ("vault/**/*.key", "vault/a\nb/x.key"),
        ],
    )
    def test_a_newline_in_a_filename_does_not_evade_an_anchored_pattern(
        self, repo: Path, pattern: str, secret: str
    ) -> None:
        """A newline must not split one listed path into two innocent ones."""
        target = repo / secret
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("TOKEN=sk-not-real\n")
        env = {
            "VOX_CALL_GUARD_ROOT": str(repo),
            "VOX_CALL_GUARD_PATTERNS": json.dumps([pattern]),
            "VOX_CALL_GUARD_GIT_IGNORES": "false",
        }
        cases = [("grep", {"pattern": "TOKEN"}, str(repo))]
        reasons = _guard(repo, cases, env)["reasons"]
        assert isinstance(reasons, list)
        assert reasons[0]

    def test_a_listing_failure_blocks_the_grep(self, repo: Path) -> None:
        """No rg anywhere: the guard cannot know what grep would read."""
        env = SecretPathPolicy.for_repo(repo).guard_env() | {
            "PI_CODING_AGENT_DIR": str(repo / "no-such-agent-dir")
        }
        cases = [("grep", {"pattern": "def"}, str(repo))]
        reasons = _guard(repo, cases, env, path_var="")["reasons"]
        assert isinstance(reasons, list)
        assert "cannot list" in str(reasons[0])


class TestEveryIgnoreSourceGitHonours:
    """A path git ignores is refused, whichever ignore file says so -- asked of git."""

    @pytest.fixture
    def nested(self, repo: Path) -> Path:
        sub = repo / "sub"
        sub.mkdir()
        (sub / ".gitignore").write_text("creds.json\n*.txt\n!keep.txt\n")
        (sub / "creds.json").write_text('{"token": "not-real"}\n')
        (sub / "notes.txt").write_text("plain words\n")
        (sub / "keep.txt").write_text("plain words\n")
        (repo / ".git" / "info" / "exclude").write_text("private.md\n")
        (repo / "private.md").write_text("not-real\n")
        return repo

    @pytest.mark.parametrize("path", ["sub/creds.json", "sub/notes.txt", "private.md"])
    def test_an_ignored_path_is_refused(self, nested: Path, path: str) -> None:
        (reason,) = _reasons(nested, [("read", {"path": path}, str(nested))])
        assert "ignored by git" in reason

    def test_a_negated_nested_pattern_is_allowed(self, nested: Path) -> None:
        case = ("read", {"path": "sub/keep.txt"}, str(nested))
        assert _reasons(nested, [case]) == [""]

    def test_a_directory_grep_that_would_read_an_ignored_file_is_refused(
        self, nested: Path
    ) -> None:
        """A glob makes rg search files git ignores; each one is asked of git."""
        args = {"pattern": "token", "path": "sub", "glob": "*.json"}
        (reason,) = _reasons(nested, [("grep", args, str(nested))])
        assert "creds.json" in reason

    def test_a_directory_grep_rg_keeps_off_ignored_files_is_allowed(
        self, nested: Path
    ) -> None:
        args = {"pattern": "plain", "path": "sub"}
        assert _reasons(nested, [("grep", args, str(nested))]) == [""]

    def test_a_git_that_cannot_answer_refuses(self, nested: Path) -> None:
        env = SecretPathPolicy.for_repo(nested).guard_env()
        cases = [("read", {"path": "sub/keep.txt"}, str(nested))]
        reasons = _guard(nested, cases, env, path_var="")["reasons"]
        assert isinstance(reasons, list)
        assert "cannot check what git ignores" in str(reasons[0])


def test_the_handler_blocks_and_terminates(repo: Path) -> None:
    env = SecretPathPolicy.for_repo(repo).guard_env()
    decision = _guard(repo, [("read", {"path": ".env.local"}, str(repo))], env)[
        "decision"
    ]
    assert isinstance(decision, dict)
    assert (decision["block"], decision["terminate"]) == (True, True)


def test_the_handler_lets_an_ordinary_read_through(repo: Path) -> None:
    env = SecretPathPolicy.for_repo(repo).guard_env()
    result = _guard(repo, [("read", {"path": ".gitignore"}, str(repo))], env)
    assert result["decision"] is None


@pytest.mark.parametrize(
    "env",
    [
        {"VOX_CALL_GUARD_ROOT": "", "VOX_CALL_GUARD_PATTERNS": "[]"},
        {"VOX_CALL_GUARD_ROOT": "/", "VOX_CALL_GUARD_PATTERNS": "not json"},
    ],
)
def test_a_misconfigured_guard_fails_to_load(repo: Path, env: dict[str, str]) -> None:
    """Fail closed at startup: pi will not start with an extension that throws."""
    result = _guard(repo, [("read", {"path": ".gitignore"}, str(repo))], env)
    assert str(result["loadError"]).startswith("vox secret guard: misconfigured")


@pytest.mark.skipif(
    shutil.which("pi") is None,
    reason="the real pi CLI is not installed; the node harness above still "
    "covers the guard's own load failure",
)
def test_real_pi_refuses_to_start_with_a_misconfigured_guard(repo: Path) -> None:
    """The fail-closed claim end to end: the call agent's own argv, real pi."""
    pi = shutil.which("pi")
    assert pi is not None
    launch = CallAgentLaunch(SecretPathPolicy.for_repo(repo), pi_bin=pi)
    env = launch.env(os.environ) | {"VOX_CALL_GUARD_PATTERNS": "not json"}
    result = subprocess.run(
        launch.argv(),
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    refused = (
        result.returncode != 0,
        "vox secret guard: misconfigured" in result.stderr,
    )
    assert refused == (True, True), result.stderr
