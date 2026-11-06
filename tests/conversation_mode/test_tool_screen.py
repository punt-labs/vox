"""Tests for :class:`ToolScreen`: the stream-side check behind the pi guard."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode import tool_screen as screen_module
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


def test_an_executed_end_with_no_start_is_treated_as_a_breach(
    screen: ToolScreen, caplog: pytest.LogCaptureFixture
) -> None:
    """Its arguments are unknown, so what it read is unknown: fail closed."""
    end = {
        "type": "tool_execution_end",
        "toolCallId": "never-started",
        "toolName": "read",
        "isError": False,
        "result": {"content": [{"type": "text", "text": "FAKE_KEY=x"}]},
    }
    with caplog.at_level(logging.WARNING, logger=screen_module.__name__):
        assert screen.breached(_record(end))
    assert "never-started" in caplog.text


def test_a_progress_update_does_not_forget_the_start(screen: ToolScreen) -> None:
    start = {
        "type": "tool_execution_start",
        "toolCallId": "c1",
        "toolName": "read",
        "args": {"path": "README.md"},
    }
    update = {"type": "tool_execution_update", "toolCallId": "c1", "toolName": "read"}
    end = {"type": "tool_execution_end", "toolCallId": "c1", "isError": False}
    breaches = [screen.breached(_record(r)) for r in (start, update, end)]
    assert breaches == [False, False, False]


def test_a_failed_end_with_no_start_read_nothing(screen: ToolScreen) -> None:
    end = {"type": "tool_execution_end", "toolCallId": "x", "isError": True}
    assert not screen.breached(_record(end))


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
    assert ToolScreen.guard_refusal(record) == "vox secret guard: .env denied"


def test_an_ordinary_tool_failure_is_not_a_guard_refusal() -> None:
    record = _record(
        {
            "type": "tool_execution_end",
            "toolCallId": "c1",
            "isError": True,
            "result": {"content": [{"type": "text", "text": "Path not found: x"}]},
        }
    )
    assert ToolScreen.guard_refusal(record) == ""


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


def test_a_grep_match_in_a_filename_with_a_colon_is_judged_whole(
    screen: ToolScreen, repo: Path
) -> None:
    """``a:.env`` is one file, matched by ``*.env`` -- not ``a`` plus a line."""
    (repo / "a:.env").write_text("KEY=x\n")
    assert _run(screen, "grep", {"pattern": "KEY"}, output="a:.env:1: KEY=x")


def test_a_grep_context_line_is_judged_too(screen: ToolScreen, repo: Path) -> None:
    (repo / "a:.env").write_text("x\nKEY=x\n")
    output = "a:.env-1- x\na:.env:2: KEY=x"
    assert _run(screen, "grep", {"pattern": "KEY", "context": "1"}, output=output)


def test_a_colon_in_the_matched_text_does_not_invent_a_path(
    screen: ToolScreen, repo: Path
) -> None:
    """The path is the prefix that exists as a file, not every colon split."""
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x")
    output = "src/a.py:1: see deploy/.env:2: for the KEY"
    assert not _run(screen, "grep", {"pattern": "KEY"}, output=output)
