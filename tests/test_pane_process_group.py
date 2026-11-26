"""Darwin's fallback must not report exit without reliable process evidence."""

from __future__ import annotations

import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from starlette.websockets import WebSocket

from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.workers.command import WorkerCommand
from punt_vox.voxd.workers.context import WorkerContext
from punt_vox.voxd.workers.pane_process_group import PaneProcessGroup
from punt_vox.voxd.workers.records import WorkerRecords

_PS = ["/bin/ps", "-ax", "-o", "pgid=,stat="]


def test_wait_refuses_its_own_process_group() -> None:
    with (
        patch("os.getpgrp", return_value=42),
        patch("os.killpg") as kill,
        pytest.raises(RuntimeError, match="own process group"),
    ):
        PaneProcessGroup(42).wait_for_exit()
    kill.assert_not_called()


def test_wait_deadline_is_bounded() -> None:
    with (
        patch("os.getpgrp", return_value=1),
        patch("os.killpg"),
        patch("time.monotonic", side_effect=[0, 4]),
        pytest.raises(RuntimeError, match="did not exit"),
    ):
        PaneProcessGroup(42).wait_for_exit()


@pytest.mark.parametrize(
    ("stdout", "stderr", "error"),
    [
        ("42 Z\n42 S\n", "", PermissionError),
        ("42 ZN\n43 S\n", "", None),
        ("", "cannot read process table\n", RuntimeError),
        ("", "", RuntimeError),
        ("42 Z\n43 S\n", "incomplete process table\n", RuntimeError),
    ],
)
def test_darwin_process_group_listing(
    stdout: str, stderr: str, error: type[Exception] | None
) -> None:
    with (
        patch("os.getpgrp", return_value=1),
        patch("platform.system", return_value="Darwin"),
        patch("os.killpg", side_effect=PermissionError),
        patch("time.monotonic", return_value=10.0),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(_PS, 0, stdout, stderr),
        ) as scan,
        ExitStack() as stack,
    ):
        if error is not None:
            stack.enter_context(pytest.raises(error))
        PaneProcessGroup(42).wait_for_exit()
    scan.assert_called_once_with(
        _PS, check=True, capture_output=True, text=True, timeout=3.0
    )


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired(_PS, 3.0), subprocess.CalledProcessError(1, _PS)],
)
def test_darwin_process_group_propagates_failed_scan(
    error: subprocess.SubprocessError,
) -> None:
    with (
        patch("os.getpgrp", return_value=1),
        patch("platform.system", return_value="Darwin"),
        patch("os.killpg", side_effect=PermissionError),
        patch("time.monotonic", return_value=10.0),
        patch("subprocess.run", side_effect=error) as scan,
        pytest.raises(type(error)) as raised,
    ):
        PaneProcessGroup(42).wait_for_exit()
    assert raised.value is error
    scan.assert_called_once_with(
        _PS, check=True, capture_output=True, text=True, timeout=3.0
    )


@pytest.mark.asyncio
async def test_orphan_scan_failure_keeps_worker_and_call_handlers_available(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    records = WorkerRecords(tmp_path / "workers")
    worker_id = str(uuid4())
    handle = f"vox-worker-{worker_id}"
    responses = [
        f"{handle}\n",
        f"VOX_WORKER_OWNER={records.namespace}:{worker_id}\n",
        "",
        "123\n",
        "",
    ]
    with (
        patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path),
        patch.object(
            WorkerCommand,
            "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, stdout, "") for stdout in responses
            ],
        ),
        patch("os.getpgid", return_value=42),
        patch("os.getpgrp", return_value=1),
        patch("os.killpg", side_effect=PermissionError),
        patch("platform.system", return_value="Darwin"),
        patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(_PS, 0.4)
        ) as scan,
        ExitStack() as lifetime,
    ):
        handlers = WorkerContext.handlers(PlaybackQueue(), lifetime)
        ws = AsyncMock(spec=WebSocket)
        await handlers["worker_list"]({"type": "worker_list", "id": "w"}, ws)
        assert ws.send_json.call_args.args[0] == {
            "type": "worker_result",
            "id": "w",
            "result": [],
        }
        await handlers["call_status"]({"type": "call_status", "id": "c"}, ws)
        assert ws.send_json.call_args.args[0] == {
            "type": "call_result",
            "id": "c",
            "result": {"state": "idle"},
        }
    scan.assert_called_once()
    assert f"Worker orphan {handle} not reaped (TimeoutExpired)" in caplog.text
