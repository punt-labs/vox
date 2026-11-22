"""Exercise the hidden CLI boundary and the extracted desktop status query."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.client_errors import VoxdProtocolError
from punt_vox.desktop_install import DesktopInstaller


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
    ],
)
def test_hidden_worker_commands_delegate(
    args: list[str], operation: str, fields: dict[str, str | bool]
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
    ("contents", "expected"),
    [
        (None, "no config"),
        ("bad json", "config unreadable"),
        ("[]", "not registered"),
        ('{"mcpServers": {"vox": {}}}', "registered"),
        ('{"mcpServers": {}}', "not registered"),
    ],
)
def test_desktop_registration_readback(
    tmp_path: Path, contents: str | None, expected: str
) -> None:
    """Preserve all four desktop states after moving readback out of the CLI."""
    path = tmp_path / "desktop.json"
    if contents is not None:
        path.write_text(contents)
    with patch.object(DesktopInstaller, "config_path", return_value=path):
        assert DesktopInstaller.registration() == expected
