"""Tests for bare ``vox call``: a thin client of voxd's Mode B call RPCs."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands.call import CallCli
from punt_vox.commands.voice_call_follow import CallFollower
from punt_vox.commands.voice_call_status import CallStatusView
from punt_vox.daemon_control import DaemonControl

runner = CliRunner(env={"COLUMNS": "200"})
_FOLLOW = "punt_vox.commands.voice_call_follow"

_LIVE: dict[str, object] = {
    "state": "live",
    "transcript": [],
    "tool_calls": [],
    "trace": "/tmp/call-trace.jsonl",
}
_ENDED: dict[str, object] = {
    "state": "ended",
    "transcript": [
        {"role": "user", "text": "Add a greeting to the demo repo."},
        {"role": "agent", "text": "Started a worker on demo."},
    ],
    "tool_calls": [{"tool": "start_worker", "arguments": {"repo": "demo"}}],
    "trace": "/tmp/call-trace.jsonl",
}


class _Voxd:
    """A scripted voxd: each reply is the next of *statuses*; the last repeats."""

    def __init__(self, *statuses: object) -> None:
        self.statuses: list[object] = list(statuses)
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def request(
        self, operation: str, *, timeout: float, **fields: object
    ) -> object:
        self.sent.append((operation, fields))
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]


class _NoPiCall:
    """A pi call lock with no live holder."""

    def is_live(self) -> bool:
        return False


@pytest.fixture
def no_poll_delay() -> Iterator[None]:
    with patch(
        "punt_vox.commands.voice_call_follow.asyncio.sleep", new_callable=AsyncMock
    ):
        yield


def _voxd(voxd: _Voxd) -> AbstractContextManager[object]:
    return patch.object(DaemonControl, "request", side_effect=voxd.request)


def _script(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "greet.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_call_teardown_requests_explicit_agent_deletion() -> None:
    voxd = _Voxd({"state": "idle"})
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), ["teardown"])

    assert result.exit_code == 0, result.output
    assert voxd.sent == [("teardown", {})]
    assert "provision a new agent" in result.output


def test_call_teardown_allows_the_serial_provider_deletion_budget() -> None:
    with patch.object(DaemonControl, "request", return_value={"state": "idle"}) as rpc:
        result = runner.invoke(CallCli.build_app(), ["teardown"])

    assert result.exit_code == 0, result.output
    assert rpc.await_args is not None
    assert rpc.await_args.kwargs["timeout"] >= 75


@pytest.mark.usefixtures("no_poll_delay")
def test_bare_call_with_a_script_starts_follows_and_reports(tmp_path: Path) -> None:
    script = _script(tmp_path, '{"pcm_path": "greet.pcm", "pause_s": 2}', "")
    voxd = _Voxd(_LIVE, _LIVE, _ENDED)
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), ["--script", str(script)])
    assert result.exit_code == 0, result.output
    operation, fields = voxd.sent[0]
    assert operation == "start"
    assert fields == {
        "script": [{"pcm_path": str(tmp_path / "greet.pcm"), "pause_s": 2}]
    }
    assert [op for op, _ in voxd.sent[1:]] == ["status", "status"]
    assert "call live | 0 tool calls" in result.output
    assert "agent: Started a worker on demo." in result.output
    assert "tool: start_worker({'repo': 'demo'}) -> ok" in result.output
    assert "trace: /tmp/call-trace.jsonl" in result.output


@pytest.mark.usefixtures("no_poll_delay")
def test_bare_call_without_a_script_starts_a_mic_call() -> None:
    voxd = _Voxd(_ENDED)
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 0, result.output
    assert voxd.sent == [("start", {})]


def test_a_failed_call_exits_one_with_its_error() -> None:
    failed: dict[str, object] = {"state": "failed", "error": "ConvAI refused"}
    voxd = _Voxd(failed)
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "error: ConvAI refused" in result.output


@pytest.mark.parametrize(
    "failure",
    [VoxdProtocolError("a call is already active"), VoxdConnectionError("down")],
)
def test_a_refusing_or_absent_voxd_exits_one(failure: Exception) -> None:
    with patch.object(DaemonControl, "request", side_effect=failure):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert f"Call unavailable: {failure}" in result.output


def test_ctrl_c_while_following_stops_the_call() -> None:
    voxd = _Voxd(_LIVE)

    async def follow(_self: CallFollower, _view: CallStatusView) -> CallStatusView:
        raise KeyboardInterrupt

    with _voxd(voxd), patch.object(CallFollower, "_follow", follow):
        voxd.statuses = [{**_ENDED, "state": "ended"}]
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 0, result.output
    assert [op for op, _ in voxd.sent] == ["start", "stop"]
    assert "agent: Started a worker on demo." in result.output


def test_ctrl_c_while_the_call_starts_hangs_it_up() -> None:
    request = AsyncMock(side_effect=[KeyboardInterrupt, _ENDED])
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 0, result.output
    assert [c.args[0] for c in request.call_args_list] == ["start", "stop"]
    assert "agent: Started a worker on demo." in result.output


def test_a_hangup_voxd_refuses_exits_one() -> None:
    request = AsyncMock(side_effect=[KeyboardInterrupt, VoxdConnectionError("down")])
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "Could not hang up: down; run `vox call stop`." in result.output


def test_status_with_voxd_down_exits_one() -> None:
    with patch.object(
        DaemonControl, "request", side_effect=VoxdConnectionError("down")
    ):
        result = runner.invoke(CallCli.build_app(), ["status"])
    assert result.exit_code == 1
    assert "Call unavailable: down" in result.output


def test_stop_hangs_up_a_running_mode_b_call(tmp_path: Path) -> None:
    voxd = _Voxd(_LIVE)
    with (
        _voxd(voxd),
        patch(
            "punt_vox.commands.call_hangup.CallLock.for_repo", return_value=_NoPiCall()
        ),
    ):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code == 0, result.output
    assert [op for op, _ in voxd.sent] == ["status", "stop"]
    assert "Stopping the call." in result.output


def test_status_prints_the_line_and_transcript() -> None:
    with _voxd(_Voxd(_ENDED)):
        result = runner.invoke(CallCli.build_app(), ["status"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == (
        "call ended | 1 tool calls | agent: Started a worker on demo."
    )
    assert "user: Add a greeting to the demo repo." in result.output


def test_script_before_a_subcommand_is_a_usage_error(tmp_path: Path) -> None:
    script = _script(tmp_path, '{"pcm_path": "a.pcm"}')
    result = runner.invoke(CallCli.build_app(), ["--script", str(script), "status"])
    assert result.exit_code == 2
    assert "applies only to bare `vox call`" in result.output


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("{not json", "line 1: Expecting property name"),
        ('{"pause_s": 1}', 'line 1: expected {"pcm_path": ...}'),
        ('["a.pcm"]', 'line 1: expected {"pcm_path": ...}'),
    ],
)
def test_malformed_script_lines_are_usage_errors(
    tmp_path: Path, line: str, message: str
) -> None:
    script = _script(tmp_path, line)
    with patch.object(DaemonControl, "request") as request:
        result = runner.invoke(CallCli.build_app(), ["--script", str(script)])
    assert result.exit_code == 2
    assert message in result.output
    request.assert_not_called()


def test_an_absolute_pcm_path_is_kept(tmp_path: Path) -> None:
    pcm = tmp_path / "elsewhere" / "turn.pcm"
    script = _script(tmp_path, json.dumps({"pcm_path": str(pcm)}))
    voxd = _Voxd(_ENDED)
    with _voxd(voxd):
        runner.invoke(CallCli.build_app(), ["--script", str(script)])
    assert voxd.sent[0][1] == {"script": [{"pcm_path": str(pcm)}]}


def test_an_unreadable_script_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(
        CallCli.build_app(), ["--script", str(tmp_path / "missing.jsonl")]
    )
    assert result.exit_code == 2
    assert "cannot read" in result.output


def test_the_status_line_truncates_long_turns() -> None:
    status: dict[str, object] = {
        "state": "live",
        "transcript": [{"role": "agent", "text": "word " * 40}],
    }
    view = CallStatusView(status)
    line = view.line()
    assert line.startswith("call live | 0 tool calls | agent: word word")
    assert line.endswith("...")
    assert len(line.split("agent: ", 1)[1]) == 72


@pytest.mark.parametrize("status", [None, {}, {"state": "stopping"}])
def test_an_unreadable_status_is_a_protocol_error(status: object) -> None:
    with pytest.raises(VoxdProtocolError):
        CallStatusView(status)


@pytest.mark.usefixtures("no_poll_delay")
@pytest.mark.parametrize(
    "lost", [VoxdConnectionError("voxd restarted"), {"state": "stopping"}]
)
def test_losing_the_status_mid_call_hangs_up_and_exits_one(lost: object) -> None:
    voxd = _Voxd(_LIVE, lost, _ENDED)

    async def request(operation: str, *, timeout: float, **fields: object) -> object:
        reply = await voxd.request(operation, timeout=timeout, **fields)
        if isinstance(reply, Exception):
            raise reply
        return reply

    with patch.object(DaemonControl, "request", side_effect=request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert [op for op, _ in voxd.sent] == ["start", "status", "stop"]
    assert "Lost the call's status, hanging up" in result.output
    assert "agent: Started a worker on demo." in result.output


def test_a_call_that_failed_to_connect_reports_its_trace() -> None:
    failed = {"state": "failed", "error": "no agent", "trace": "/tmp/t.jsonl"}
    request = AsyncMock(side_effect=[VoxdProtocolError("did not connect"), failed])
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "Call unavailable: did not connect" in result.output
    assert "error: no agent" in result.output
    assert "trace: /tmp/t.jsonl" in result.output


def test_a_start_refused_beside_a_live_call_reports_no_other_call() -> None:
    request = AsyncMock(side_effect=[VoxdProtocolError("already running"), _ENDED])
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "Started a worker" not in result.output


@pytest.mark.usefixtures("no_poll_delay")
def test_following_stops_when_voxd_reports_another_call() -> None:
    first = {**_LIVE, "trace": "/tmp/first.jsonl"}
    voxd = _Voxd(first, {**_LIVE, "trace": "/tmp/second.jsonl"})
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 0, result.output
    assert [op for op, _ in voxd.sent] == ["start", "status"]
    assert "trace: /tmp/first.jsonl" in result.output
    assert "second" not in result.output


def test_ctrl_c_exits_one_when_voxd_still_reports_the_call_live() -> None:
    voxd = _Voxd(_LIVE)

    async def follow(_self: CallFollower, _view: CallStatusView) -> CallStatusView:
        raise KeyboardInterrupt

    with _voxd(voxd), patch.object(CallFollower, "_follow", follow):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "voxd still reports the call live" in result.output


def test_a_second_ctrl_c_while_hanging_up_exits_130() -> None:
    request = AsyncMock(side_effect=[_LIVE, KeyboardInterrupt])

    async def follow(_self: CallFollower, _view: CallStatusView) -> CallStatusView:
        raise KeyboardInterrupt

    with (
        patch.object(DaemonControl, "request", request),
        patch.object(CallFollower, "_follow", follow),
    ):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 130
    assert "the call may still be live" in result.output


@pytest.mark.asyncio
async def test_daemon_control_checks_the_reply_family() -> None:
    transport = AsyncMock()

    async def reply(request: dict[str, object], **_: object) -> dict[str, object]:
        assert request["type"] == "call_status"
        return {"type": "worker_result", "id": request["id"], "result": {}}

    transport.send_and_recv.side_effect = reply
    with (
        patch("punt_vox.daemon_control.VoxdTransport", return_value=transport),
        pytest.raises(VoxdProtocolError, match="Unexpected call reply"),
    ):
        await DaemonControl("call").request("status", timeout=1)
    transport.close.assert_awaited_once()


def test_a_terminal_status_line_is_clipped_to_the_terminal_width(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wrapped line would strand rows the next in-place rewrite cannot reach."""
    with (
        patch(
            "punt_vox.commands.voice_call_follow.sys.stdout.isatty", return_value=True
        ),
        patch(
            f"{_FOLLOW}.shutil.get_terminal_size",
            return_value=os.terminal_size((40, 24)),
        ),
    ):
        CallFollower._show("call live | 1 tool calls | agent: " + "word " * 20)

    written = capsys.readouterr().out
    assert written.startswith("\r\x1b[K")
    assert len(written.removeprefix("\r\x1b[K")) == 39
    assert written.endswith("...")
