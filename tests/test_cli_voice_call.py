"""Tests for bare ``vox call``: a thin client of voxd's call RPCs."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from click import unstyle
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
    "call_id": "first",
    "transcript": [],
    "tool_calls": [],
    "trace": "/tmp/call-trace.jsonl",
}
_ENDED: dict[str, object] = {
    "state": "ended",
    "call_id": "first",
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


def test_call_start_no_longer_routes_to_the_pi_agent() -> None:
    with patch.object(DaemonControl, "request", new_callable=AsyncMock) as rpc:
        result = runner.invoke(CallCli.build_app(), ["start", "--context", "-"])

    assert result.exit_code == 2, result.output
    assert "No such command 'start'" in unstyle(result.output)
    rpc.assert_not_awaited()


@pytest.mark.parametrize("state", ["idle", "ended"])
def test_call_teardown_requests_explicit_agent_deletion(state: str) -> None:
    voxd = _Voxd({"state": state})
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
    assert operation == "start_scoped"
    assert UUID(str(fields.pop("call_id"))).version == 4
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
    assert [op for op, _ in voxd.sent] == ["start_scoped"]
    assert set(voxd.sent[0][1]) == {"call_id"}


@pytest.mark.parametrize(("flag", "mode"), [("--headset", "headset"), ("--aec", "aec")])
def test_bare_call_forwards_echo_declaration(flag: str, mode: str) -> None:
    voxd = _Voxd(_ENDED)
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), [flag])

    assert result.exit_code == 0, result.output
    assert [op for op, _ in voxd.sent] == ["start_scoped"]
    assert voxd.sent[0][1]["echo_control"] == mode
    bad = runner.invoke(CallCli.build_app(), [flag, "status"])
    assert bad.exit_code == 2
    assert "applies only to bare" in bad.output


def test_a_failed_call_exits_one_with_its_error() -> None:
    failed: dict[str, object] = {**_ENDED, "state": "failed", "error": "ConvAI refused"}
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
    voxd = _Voxd(_LIVE, _ENDED)

    async def follow(_self: CallFollower, _view: CallStatusView) -> CallStatusView:
        raise KeyboardInterrupt

    with _voxd(voxd), patch.object(CallFollower, "_follow", follow):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 0, result.output
    assert voxd.sent[1:] == [("stop_scoped", {"call_id": _LIVE["call_id"]})]
    assert "agent: Started a worker on demo." in result.output


def test_ctrl_c_while_the_call_starts_hangs_it_up() -> None:
    request = AsyncMock(side_effect=[KeyboardInterrupt, _ENDED])
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 0, result.output
    assert [c.args[0] for c in request.call_args_list] == [
        "start_scoped",
        "stop_scoped",
    ]
    start, stop = request.await_args_list
    assert stop.kwargs["call_id"] == start.kwargs["call_id"]
    assert UUID(start.kwargs["call_id"]).version == 4
    assert "agent: Started a worker on demo." in result.output


def test_a_hangup_voxd_refuses_exits_one() -> None:
    request = AsyncMock(side_effect=[KeyboardInterrupt, VoxdConnectionError("down")])
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "Could not hang up: down; run `vox call status`." in result.output


def test_status_with_voxd_down_exits_one() -> None:
    with patch.object(
        DaemonControl, "request", side_effect=VoxdConnectionError("down")
    ):
        result = runner.invoke(CallCli.build_app(), ["status"])
    assert result.exit_code == 1
    assert "Call unavailable: down" in result.output


def test_stop_hangs_up_the_running_call() -> None:
    voxd = _Voxd(_LIVE)
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code == 0, result.output
    assert voxd.sent == [
        ("status", {}),
        ("stop_scoped", {"call_id": _LIVE["call_id"]}),
    ]
    assert "Stopping the call." in result.output


def test_stop_does_not_send_hangup_when_no_call_is_active() -> None:
    voxd = _Voxd({"state": "idle"})
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), ["stop"])

    assert result.exit_code == 2, result.output
    assert "no call is active" in result.output
    assert voxd.sent == [("status", {})]


def test_stop_refuses_a_live_call_without_an_id() -> None:
    voxd = _Voxd({"state": "live"})
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), ["stop"])

    assert result.exit_code == 1, result.output
    assert "upgrade or restart voxd" in result.output
    assert voxd.sent == [("status", {})]


def test_stop_does_not_retry_when_the_call_was_replaced() -> None:
    request = AsyncMock(
        side_effect=[
            _LIVE,
            VoxdProtocolError("the requested call is no longer current"),
        ]
    )
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code == 1
    assert "Call unavailable: the requested call is no longer current" in result.output
    assert [item.args[0] for item in request.await_args_list] == [
        "status",
        "stop_scoped",
    ]
    assert request.await_args_list[1].kwargs["call_id"] == _LIVE["call_id"]


def test_start_refuses_an_old_daemon_without_dialing_a_legacy_call() -> None:
    request = AsyncMock(
        side_effect=VoxdProtocolError("unknown message type: call_start_scoped")
    )
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "upgrade or restart voxd" in result.output
    request.assert_awaited_once()
    assert request.await_args is not None
    assert request.await_args.args == ("start_scoped",)


def test_status_prints_the_line_and_transcript() -> None:
    with _voxd(_Voxd(_ENDED)):
        result = runner.invoke(CallCli.build_app(), ["status"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == (
        "call ended | 1 tool calls | agent: Started a worker on demo."
    )
    assert "user: Add a greeting to the demo repo." in result.output


@pytest.mark.parametrize("state", ["idle", "live"])
def test_status_displays_a_reply_without_a_call_id(state: str) -> None:
    voxd = _Voxd({"state": state})
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), ["status"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"call {state} | 0 tool calls"
    assert voxd.sent == [("status", {})]


def test_display_status_still_refuses_a_malformed_id() -> None:
    with _voxd(_Voxd({**_LIVE, "call_id": " "})):
        result = runner.invoke(CallCli.build_app(), ["status"])

    assert result.exit_code == 1, result.output
    assert "Invalid call ID" in result.output


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
    assert voxd.sent[0][1]["script"] == [{"pcm_path": str(pcm)}]


def test_an_unreadable_script_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(
        CallCli.build_app(), ["--script", str(tmp_path / "missing.jsonl")]
    )
    assert result.exit_code == 2
    assert "cannot read" in result.output


def test_the_status_line_truncates_long_turns() -> None:
    status: dict[str, object] = {
        **_LIVE,
        "transcript": [{"role": "agent", "text": "word " * 40}],
    }
    view = CallStatusView(status)
    line = view.line()
    assert line.startswith("call live | 0 tool calls | agent: word word")
    assert line.endswith("...")
    assert len(line.split("agent: ", 1)[1]) == 72


@pytest.mark.parametrize(
    "status",
    [None, {}, {"state": "stopping"}, {**_LIVE, "call_id": 1}],
)
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
    assert voxd.sent[1:] == [
        ("status", {}),
        ("stop_scoped", {"call_id": _LIVE["call_id"]}),
    ]
    assert "Lost the call's status, hanging up" in result.output
    assert "agent: Started a worker on demo." in result.output


def test_a_call_that_failed_to_connect_reports_its_trace() -> None:
    failed = {**_ENDED, "state": "failed", "error": "no agent", "trace": "/tmp/t.jsonl"}
    request = AsyncMock(return_value=failed)
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "Call unavailable" in result.output
    assert "error: no agent" in result.output
    assert "trace: /tmp/t.jsonl" in result.output


def test_a_start_refused_beside_a_live_call_reports_no_other_call() -> None:
    request = AsyncMock(side_effect=[VoxdProtocolError("already running"), _ENDED])
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "Started a worker" not in result.output


def test_echo_refusal_does_not_read_a_previous_failed_call() -> None:
    request = AsyncMock(
        side_effect=VoxdProtocolError("Live calls require echo protection")
    )
    with patch.object(DaemonControl, "request", request):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 1
    assert "Live calls require echo protection" in result.output
    request.assert_awaited_once()
    assert request.await_args is not None
    assert request.await_args.args == ("start_scoped",)


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--headset", "--aec"], "mutually exclusive"),
        (["--script", "unused.jsonl", "--headset"], "apply only to live calls"),
    ],
)
def test_conflicting_call_input_flags_are_rejected(
    flags: list[str], message: str
) -> None:
    result = runner.invoke(CallCli.build_app(), flags)
    assert result.exit_code == 2
    assert message in result.output


@pytest.mark.usefixtures("no_poll_delay")
def test_following_stops_when_voxd_reports_another_call() -> None:
    first = {**_LIVE, "trace": "/tmp/first.jsonl"}
    voxd = _Voxd(first, {**_LIVE, "call_id": "second", "trace": "/tmp/second.jsonl"})
    with _voxd(voxd):
        result = runner.invoke(CallCli.build_app(), [])
    assert result.exit_code == 0, result.output
    assert [op for op, _ in voxd.sent] == ["start_scoped", "status"]
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


@pytest.mark.parametrize("text", ["short call status", "word " * 20])
def test_a_terminal_status_line_fits_the_terminal_width(
    capsys: pytest.CaptureFixture[str],
    text: str,
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
        CallFollower._show(text)

    written = capsys.readouterr().out
    assert written.startswith("\r\x1b[K")
    expected = text if len(text) <= 39 else text[:36] + "..."
    assert written.removeprefix("\r\x1b[K") == expected


def test_terminal_call_report_starts_on_a_new_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch(f"{_FOLLOW}.sys.stdout.isatty", return_value=True):
        CallFollower.finish(CallStatusView(_ENDED))

    assert capsys.readouterr().out.startswith("\n")
