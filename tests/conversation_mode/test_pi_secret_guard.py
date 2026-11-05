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

from punt_vox.voxd.conversation_mode.call_agent_launch import SECRET_GUARD
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy

pytestmark = pytest.mark.subprocess

# Imports the guard, then answers each [tool, input, cwd] case with the
# reason the guard gives ("" when allowed), and drives the default export's
# tool_call handler once with the first case.
_HARNESS = """
const [guardUrl, casesJson] = process.argv.slice(1);
const mod = await import(guardUrl);
const cases = JSON.parse(casesJson);
let reasons = null;
try {
  const guard = mod.createGuard({
    root: process.env.VOX_CALL_GUARD_ROOT,
    patterns: JSON.parse(process.env.VOX_CALL_GUARD_PATTERNS),
  });
  reasons = cases.map(([tool, input, cwd]) => guard(tool, input, cwd));
} catch {
  // A misconfigured guard: only the handler's fail-closed answer is checked.
}
let handler;
mod.default({ on: (event, fn) => { if (event === "tool_call") handler = fn; } });
const [tool, input, cwd] = cases[0];
const decision = handler({ toolName: tool, input }, { cwd }) ?? null;
console.log(JSON.stringify({ reasons, decision }));
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".punt-labs" / "ethos").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".gitignore").write_text(".punt-labs/**/*.local.*\n.envrc.local\n")
    (root / ".env.local").write_text("FAKE_API_KEY=not-real\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the repo")
    (root / "innocent.txt").symlink_to(outside)
    return root


def _guard(
    repo: Path, cases: list[tuple[str, dict[str, str], str]], env: dict[str, str]
) -> dict[str, object]:
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
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env},
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
        "/proc/self/environ",
        "~/.claude/.credentials.json",
        "~/.config/gh/hosts.yml",
        "innocent.txt",
        "../outside.txt",
        "src/../../outside.txt",
        "file:///etc/passwd",
        ".punt-labs/ethos/claude.local.yaml",
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


def test_a_missing_configuration_blocks_every_call(repo: Path) -> None:
    """Fail closed: no root and no patterns means nothing is readable."""
    env = {"VOX_CALL_GUARD_ROOT": "", "VOX_CALL_GUARD_PATTERNS": "[]"}
    result = _guard(repo, [("read", {"path": ".gitignore"}, str(repo))], env)
    decision = result["decision"]
    assert isinstance(decision, dict)
    assert decision["block"] is True
