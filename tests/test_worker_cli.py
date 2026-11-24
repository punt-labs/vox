"""Exercise the hidden worker CLI boundary."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.client_errors import VoxdProtocolError


def test_relative_worker_project_is_resolved_in_the_callers_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with patch(
        "punt_vox.worker_client.WorkerClient.request",
        new_callable=AsyncMock,
        return_value=[],
    ) as request:
        result = CliRunner().invoke(
            app, ["worker", "start", "--task", "edit", "--project", "repo"]
        )
    assert result.exit_code == 0, result.output
    assert request.call_args.kwargs["project"] == str(tmp_path / "repo")


@pytest.mark.parametrize(
    ("args", "operation", "fields"),
    [
        (
            ["worker", "start", "--task", "edit", "--project", "/repo"],
            "start",
            {
                "task": "edit",
                "project": "/repo",
                "provider": "tmux",
                "harness": "claude",
            },
        ),
        (["worker", "list"], "list", {}),
        (["worker", "stop", "worker-id"], "stop", {"worker_id": "worker-id"}),
        (
            ["worker", "steer", "worker-id", " literal ", "--urgent"],
            "steer",
            {"worker_id": "worker-id", "words": " literal ", "urgent": True},
        ),
        (
            ["worker", "steer", "worker-id", "words"],
            "steer",
            {"worker_id": "worker-id", "words": "words", "urgent": False},
        ),
        (
            [
                "worker",
                "steer",
                "worker-id",
                "Yes",
                "--choice",
                "approve",
                "--prompt-seq",
                "3",
            ],
            "steer",
            {
                "worker_id": "worker-id",
                "words": "Yes",
                "urgent": False,
                "choice": "approve",
                "prompt_seq": 3,
            },
        ),
    ],
)
def test_hidden_worker_commands_delegate(
    args: list[str], operation: str, fields: dict[str, str | bool | int]
) -> None:
    """All commands are thin RPC adapters and print the daemon's result."""
    with patch(
        "punt_vox.worker_client.WorkerClient.request",
        new_callable=AsyncMock,
        return_value=[],
    ) as request:
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []
    request.assert_awaited_once_with(operation, **fields)
    assert "worker" not in CliRunner().invoke(app, ["--help"]).output


def test_worker_cli_reports_daemon_refusal() -> None:
    """The user sees the policy refusal, not an exception traceback."""
    with patch(
        "punt_vox.worker_client.WorkerClient.request",
        new_callable=AsyncMock,
        side_effect=VoxdProtocolError("not allowlisted"),
    ):
        result = CliRunner().invoke(app, ["worker", "list"])
    assert result.exit_code == 1
    assert "not allowlisted" in result.output


@pytest.mark.parametrize(
    ("args", "operation"),
    [
        (["worker", "allow", "repo"], "allow"),
        (["worker", "disallow", "repo"], "disallow"),
    ],
)
def test_allow_and_disallow_send_the_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], operation: str
) -> None:
    """The CLI resolves the path; the daemon validates and writes the allowlist."""
    monkeypatch.chdir(tmp_path)
    with patch(
        "punt_vox.worker_client.WorkerClient.request",
        new_callable=AsyncMock,
        return_value=[str(tmp_path / "repo")],
    ) as request:
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    request.assert_awaited_once_with(operation, project=str(tmp_path / "repo"))
    assert result.output == f"{tmp_path / 'repo'}\n"


def test_repos_lists_one_path_per_line_or_a_json_array() -> None:
    with patch(
        "punt_vox.worker_client.WorkerClient.request",
        new_callable=AsyncMock,
        return_value=["/a", "/b"],
    ) as request:
        text = CliRunner().invoke(app, ["worker", "repos"])
        as_json = CliRunner().invoke(app, ["worker", "repos", "--json"])
    assert text.exit_code == 0, text.output
    assert text.output == "/a\n/b\n"
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == ["/a", "/b"]
    assert request.await_args_list == [call("repos"), call("repos")]


def test_allow_refusal_is_one_line_on_stderr() -> None:
    with patch(
        "punt_vox.worker_client.WorkerClient.request",
        new_callable=AsyncMock,
        side_effect=VoxdProtocolError("not a git repository top level: /x"),
    ):
        result = CliRunner().invoke(app, ["worker", "allow", "/x"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "vox: worker allow: not a git repository top level: /x\n"


def test_worker_help_names_the_allowlist_commands() -> None:
    output = CliRunner().invoke(app, ["worker", "--help"]).output
    for command in ("allow", "disallow", "repos"):
        assert command in output


@pytest.mark.parametrize("blank", ["", "   "])
def test_allow_and_disallow_refuse_a_blank_path_before_sending(blank: str) -> None:
    """Path('') is the cwd; the daemon's blank check must see the blank."""
    with patch(
        "punt_vox.worker_client.WorkerClient.request", new_callable=AsyncMock
    ) as request:
        allow = CliRunner().invoke(app, ["worker", "allow", blank])
        disallow = CliRunner().invoke(app, ["worker", "disallow", blank])
    request.assert_not_awaited()
    for result, name in ((allow, "allow"), (disallow, "disallow")):
        assert result.exit_code == 2, result.output
        assert f"vox: worker {name}: PATH must not be blank" in result.output
