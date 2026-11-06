"""Tests for :class:`ToolScreen`: the stream-side check behind the pi guard."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy
from punt_vox.voxd.conversation_mode.tool_screen import ToolScreen


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".punt-labs" / "ethos").mkdir(parents=True)
    (root / ".gitignore").write_text(".punt-labs/**/*.local.*\n")
    return root


@pytest.fixture
def screen(repo: Path) -> ToolScreen:
    return ToolScreen(SecretPathPolicy.for_repo(repo), repo)


def _record(fields: Mapping[str, object]) -> PiRpcRecord:
    return PiRpcRecord.parse(json.dumps(fields).encode())


def _run(
    screen: ToolScreen,
    name: str,
    args: dict[str, object],
    *,
    output: str = "ok",
    is_error: bool = False,
) -> bool:
    """Feed one tool call's start and end; return whether the end breached."""
    start = {
        "type": "tool_execution_start",
        "toolCallId": "c1",
        "toolName": name,
        "args": args,
    }
    assert not screen.breached(_record(start))
    end = {
        "type": "tool_execution_end",
        "toolCallId": "c1",
        "toolName": name,
        "isError": is_error,
        "result": {"content": [{"type": "text", "text": output}]},
    }
    return screen.breached(_record(end))


def test_an_executed_read_of_env_is_a_breach(screen: ToolScreen) -> None:
    assert _run(screen, "read", {"path": "@.env.local"})


def test_a_blocked_read_of_env_is_not_a_breach(screen: ToolScreen) -> None:
    """The guard refused it: nothing was read, so the call carries on."""
    assert not _run(screen, "read", {"path": ".env"}, is_error=True)


def test_an_executed_read_outside_the_repo_is_a_breach(screen: ToolScreen) -> None:
    assert _run(screen, "read", {"path": "/proc/self/environ"})


def test_an_executed_ordinary_read_is_fine(screen: ToolScreen) -> None:
    assert not _run(screen, "read", {"path": "README.md"})


def test_a_grep_glob_aimed_at_env_files_is_a_breach(screen: ToolScreen) -> None:
    assert _run(screen, "grep", {"pattern": "KEY", "glob": "*.env*"})


def test_a_broad_grep_landing_in_env_is_a_breach(screen: ToolScreen) -> None:
    assert _run(screen, "grep", {"pattern": "KEY"}, output="config/.env:3: KEY=x")


def test_a_subdirectory_grep_is_judged_relative_to_its_search_path(
    screen: ToolScreen,
) -> None:
    """``x.local.yaml`` from a grep of ``.punt-labs/ethos`` is
    ``.punt-labs/ethos/x.local.yaml`` -- the anchored gitignore pattern's
    target -- not ``x.local.yaml`` at the repo root."""
    assert _run(
        screen,
        "grep",
        {"pattern": "token", "path": ".punt-labs/ethos"},
        output="x.local.yaml:1: token: abc",
    )


def test_an_ordinary_grep_is_fine(screen: ToolScreen) -> None:
    assert not _run(screen, "grep", {"pattern": "def"}, output="src/a.py:1: def f")


def test_a_find_pattern_aimed_at_keys_is_a_breach(screen: ToolScreen) -> None:
    assert _run(screen, "find", {"pattern": "**/*.pem"})


def test_records_that_are_not_tool_ends_never_breach(screen: ToolScreen) -> None:
    assert not screen.breached(_record({"type": "agent_settled"}))


def test_a_guard_block_is_recognised_as_a_refusal() -> None:
    record = _record(
        {
            "type": "tool_execution_end",
            "toolCallId": "c1",
            "isError": True,
            "result": {
                "content": [{"type": "text", "text": "vox secret guard: .env denied"}]
            },
        }
    )
    assert ToolScreen.guard_refused(record)


def test_an_ordinary_tool_failure_is_not_a_guard_refusal() -> None:
    record = _record(
        {
            "type": "tool_execution_end",
            "toolCallId": "c1",
            "isError": True,
            "result": {"content": [{"type": "text", "text": "Path not found: x"}]},
        }
    )
    assert not ToolScreen.guard_refused(record)


def test_a_directory_grep_that_read_a_non_ignored_envrc_is_a_breach(
    screen: ToolScreen,
) -> None:
    """Behind the guard's pre-grep listing: if an .envrc match ever came back,
    the call ends."""
    assert _run(screen, "grep", {"pattern": "TOKEN"}, output=".envrc:1: export TOKEN=x")


def test_a_negated_glob_grep_landing_in_a_secret_is_a_breach(
    screen: ToolScreen,
) -> None:
    """``!*.py`` widens the search; the output, not the glob, shows what it read."""
    assert _run(
        screen,
        "grep",
        {"pattern": "TOKEN", "glob": "!*.py"},
        output="deploy/.env.production:2: TOKEN=x",
    )


def test_a_negated_glob_alone_is_not_a_breach(screen: ToolScreen) -> None:
    assert not _run(
        screen, "grep", {"pattern": "def", "glob": "!*.md"}, output="a.py:1: def f"
    )
