"""Coverage for sender stamping, live ingest, and curated context inspection."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TextIO, cast
from unittest.mock import AsyncMock, patch

import pytest
import typer
from starlette.testclient import TestClient
from typer.testing import CliRunner

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.commands.call import CallCli
from punt_vox.hook_context_client import HookContextClient
from punt_vox.hook_context_relay import HookContextCommand, HookContextRelay, main
from punt_vox.hook_sequence import HookSequence
from punt_vox.voxd.daemon import build_app
from punt_vox.voxd.hook_context_store import HookContextStore


def _event(event: str, sequence: int, **fields: object) -> dict[str, object]:
    return {
        "session_id": "session-a",
        "hook_event_name": event,
        "relay_seq": sequence,
        **fields,
    }


def test_worker_sequence_excludes_unadmitted_context_and_is_monotonic() -> None:
    store = HookContextStore()
    assert store.worker_sequence("session-a") == 0
    with pytest.raises(ValueError, match="no hook context"):
        store.snapshot("session-a")
    store.ingest(_event("PostToolUse", 10))
    assert store.worker_sequence("session-a") == 0
    store.ingest(_event("Stop", 6), worker_input=True)
    store.ingest(_event("PostToolUse", 5), worker_input=True)
    assert store.worker_sequence("session-a") == 6
    assert store.snapshot("session-a")["gaps"]["highest_seen"] == 10
    store.ingest(_event("PostToolUse", 12))
    assert store.worker_sequence("session-a") == 6
    with pytest.raises(ValueError, match="relay_seq"):
        store.ingest(_event("Stop", 0), worker_input=True)
    assert store.worker_sequence("session-a") == 6
    store.ingest(_event("Stop", 11), worker_input=True)
    assert store.worker_sequence("session-a") == 11


def test_counter_serializes_concurrent_independent_opens(tmp_path: Path) -> None:
    """Every sender gets a unique sequence in its own session."""
    counter = HookSequence(tmp_path, "session/a")

    def stamp(_: int) -> int:
        return counter.next()

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(stamp, range(32)))
    assert sorted(values) == list(range(1, 33))
    assert HookSequence(tmp_path, "session_a").next() == 1
    assert HookSequence(tmp_path, "session/a").next() == 33


def test_counter_fsync_failure_preserves_last_committed_sequence(
    tmp_path: Path,
) -> None:
    """An interruption before replacement cannot reset the published counter."""
    assert HookSequence(tmp_path, "a").next() == 1
    with (
        patch("punt_vox.atomic_file.os.fsync", side_effect=InterruptedError),
        pytest.raises(InterruptedError),
    ):
        HookSequence(tmp_path, "a").next()
    assert HookSequence(tmp_path, "a").next() == 2


@pytest.mark.parametrize("worker_only", [False, True])
def test_relay_stages_without_waiting_for_delivery(
    tmp_path: Path, worker_only: bool
) -> None:
    """The hook returns after allocating a sequence and starting a child."""
    staged: list[object] = []

    def capture(args: list[str], **kwargs: object) -> None:
        assert args[1:] == ["-E", "-P", "-m", "punt_vox.hook_context_relay"]
        assert kwargs["start_new_session"] is True
        assert kwargs["cwd"] == tmp_path
        source = cast("TextIO", kwargs["stdin"])
        with os.fdopen(os.dup(source.fileno()), encoding="utf-8") as stream:
            staged.append(json.load(stream))

    with (
        patch("punt_vox.hook_context_relay.user_state_dir", return_value=tmp_path),
        patch("punt_vox.hook_context_relay.subprocess.Popen", side_effect=capture),
    ):
        HookContextRelay(
            {
                "session_id": "a",
                "hook_event_name": "Stop",
                "nested": [{"Api-Key": "secret-value"}],
            }
        ).submit(worker_only=worker_only)
    assert staged == [
        {
            "session_id": "a",
            "hook_event_name": "Stop",
            "relay_seq": 1,
            "nested": [{"Api-Key": "[redacted]"}],
            **({"_vox_worker_only": True} if worker_only else {}),
        }
    ]


def test_relay_compacts_a_large_tool_result_without_losing_retry_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The successful retry fits on the wire and clears the earlier failure."""
    store = HookContextStore()
    tool = {"tool_name": "Read", "tool_input": {"file_path": "large.txt"}}
    store.ingest(_event("PostToolUseFailure", 1, **tool, error="missing file"))
    assert HookSequence(tmp_path / "hook-sequences", "session-a").next() == 1

    def capture(_args: list[str], **kwargs: object) -> None:
        source = cast("TextIO", kwargs["stdin"])
        encoded = source.read()
        assert len(encoded) < 256 * 1024
        store.ingest(json.loads(encoded))

    monkeypatch.setattr("punt_vox.hook_context_relay.user_state_dir", lambda: tmp_path)
    monkeypatch.setattr("punt_vox.hook_context_relay.subprocess.Popen", capture)
    HookContextRelay(
        {
            "session_id": "session-a",
            "hook_event_name": "PostToolUse",
            **tool,
            "tool_response": {"stdout": "x" * (256 * 1024) + "finished"},
        }
    ).submit()
    snapshot = store.snapshot("session-a")
    assert snapshot["open_failure"] is None
    assert snapshot["active_files"] == ["large.txt"]
    assert snapshot["last_results"][-1]["result"].endswith("finished")
    assert snapshot["gaps"]["missing_count"] == 0


def test_relay_distinguishes_credential_inputs_without_retaining_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different redacted credential cannot clear a failure."""
    store = HookContextStore()

    def capture(_args: list[str], **kwargs: object) -> None:
        source = cast("TextIO", kwargs["stdin"])
        encoded = source.read()
        assert "first-example" not in encoded
        assert "second-example" not in encoded
        store.ingest(json.loads(encoded))

    monkeypatch.setattr("punt_vox.hook_context_relay.user_state_dir", lambda: tmp_path)
    monkeypatch.setattr("punt_vox.hook_context_relay.subprocess.Popen", capture)
    for event, password, has_failure in (
        ("PostToolUseFailure", "first-example", True),
        ("PostToolUse", "second-example", True),
        ("PostToolUse", "first-example", False),
    ):
        HookContextRelay(
            {
                "session_id": "session-a",
                "hook_event_name": event,
                "tool_name": "Example",
                "tool_input": {"password": password},
            }
        ).submit()
        assert (store.snapshot("session-a")["open_failure"] is not None) is has_failure


@pytest.mark.parametrize(
    ("event", "field", "projected"),
    [
        ("UserPromptSubmit", "prompt", "goal"),
        ("Stop", "last_assistant_message", "last_assistant_message"),
        ("Notification", "message", None),
    ],
)
def test_oversized_progress_preserves_context_and_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    field: str,
    projected: str | None,
) -> None:
    """Compaction keeps relevant text or just the progress envelope."""
    store = HookContextStore()

    def capture(_args: list[str], **kwargs: object) -> None:
        source = cast("TextIO", kwargs["stdin"])
        encoded = source.read()
        assert len(encoded) < 256 * 1024
        store.ingest(json.loads(encoded))

    monkeypatch.setattr("punt_vox.hook_context_relay.user_state_dir", lambda: tmp_path)
    monkeypatch.setattr("punt_vox.hook_context_relay.subprocess.Popen", capture)
    HookContextRelay(
        {
            "session_id": "session-a",
            "hook_event_name": event,
            field: "x" * (256 * 1024),
        }
    ).submit()
    snapshot = store.snapshot("session-a")
    assert snapshot["gaps"]["highest_seen"] == 1
    assert snapshot["goal"] == ("x" * 1000 + "..." if projected == "goal" else None)
    assert snapshot["last_assistant_message"] == (
        "x" * 1000 + "..." if projected == "last_assistant_message" else None
    )


def test_relay_rejects_already_stamped_payload() -> None:
    """A stamped event is never replayed as a new unstamped hook."""
    with pytest.raises(ValueError, match="already stamped"):
        HookContextRelay(_event("Stop", 1)).submit()


def test_counter_exhaustion_does_not_wrap(tmp_path: Path) -> None:
    """At the maximum relay sequence, stamping fails instead of reusing one."""
    counter = HookSequence(tmp_path, "session-a")
    assert counter.next() == 1
    path = next(tmp_path.glob("*.seq"))
    path.write_text(str(2**63 - 1), encoding="ascii")
    with pytest.raises(ValueError, match="invalid hook counter"):
        counter.next()
    assert path.read_text(encoding="ascii") == str(2**63 - 1)


def test_stamping_failure_is_on_stderr(capfd: pytest.CaptureFixture[str]) -> None:
    """A failed stamp cannot be mistaken for a successfully relayed event."""
    command = HookContextCommand(lambda: {"session_id": "a", "hook_event_name": "Stop"})
    with (
        patch("punt_vox.hook_context_relay.HookSequence.next", side_effect=OSError),
        pytest.raises(typer.Exit) as failure,
    ):
        command.run()
    assert failure.value.exit_code == 1
    stdout, stderr = capfd.readouterr()
    assert stdout == ""
    assert "stamping or staging failed: OSError" in stderr


def test_corrupt_counter_recovery_does_not_reset_the_sequence(tmp_path: Path) -> None:
    """An invalid counter cannot restart sequence numbering silently."""
    counter = HookSequence(tmp_path, "a")
    assert counter.next() == 1
    (path,) = tmp_path.glob("*.seq")
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="start a new session instead of resuming"):
        counter.next()
    assert path.read_bytes() == b"\xff"


def test_detached_relay_logs_nesting_failure_without_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The detached child records a failure without logging hook content."""
    with (
        patch("punt_vox.hook_context_relay.configure_client_logging"),
        patch(
            "punt_vox.hook_context_relay.json.load",
            side_effect=RecursionError("private"),
        ),
    ):
        assert main() == 1
    assert "hook context delivery failed: RecursionError" in caplog.text
    assert "private" not in caplog.text


def test_enabled_boundary(tmp_path: Path) -> None:
    """An unenabled repository requires daemon admission before sending an event."""
    (tmp_path / ".git").mkdir()
    command = HookContextCommand(
        lambda: {"cwd": str(tmp_path), "session_id": "a", "hook_event_name": "Stop"}
    )
    with patch.object(HookContextRelay, "submit") as submit:
        command.run(enabled_only=True)
    submit.assert_called_once_with(worker_only=True)


def test_enabled_boundary_rejects_symlink(tmp_path: Path) -> None:
    """A symlink cannot opt a repository into context fanout."""
    (tmp_path / ".git").mkdir()
    config = tmp_path / ".punt-labs" / "vox"
    config.mkdir(parents=True)
    target = tmp_path / "unrelated"
    target.touch()
    marker = config / "enabled"
    marker.symlink_to(target)
    relay = HookContextRelay({"cwd": str(tmp_path)})
    assert not relay.enabled()
    marker.unlink()
    marker.touch()
    assert relay.enabled()


def test_global_hook_ignores_empty_input() -> None:
    """With no payload, the global hook has no attribution or opt-in."""
    command = HookContextCommand(dict)
    with patch.object(HookContextRelay, "submit") as submit:
        command.run(enabled_only=True)
    submit.assert_not_called()


def test_shell_hook_delegates_before_stdin_closes(tmp_path: Path) -> None:
    """The shell must invoke the CLI even when the harness leaves stdin open."""
    vox = tmp_path / "vox"
    vox.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    vox.chmod(0o700)
    script = Path(__file__).resolve().parents[1] / "plugin/hooks/context-event.sh"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    with subprocess.Popen(
        [str(script)], cwd=tmp_path, env=env, stdin=subprocess.PIPE
    ) as hook:
        try:
            assert hook.wait(timeout=3) == 42
        finally:
            if hook.poll() is None:
                hook.kill()


def test_advance_and_heal_gap_without_overwriting_newer_fields() -> None:
    """A late arrival repairs a gap; a duplicate cannot mutate the view."""
    store = HookContextStore()
    store.ingest(_event("UserPromptSubmit", 4, prompt="new"))
    assert store.snapshot("session-a")["gaps"] == {
        "highest_seen": 4,
        "missing_count": 3,
        "missing_ranges": [(1, 3)],
        "untracked_through": 0,
    }
    store.ingest(_event("UserPromptSubmit", 2, prompt="old"))
    before_duplicate = store.snapshot("session-a")
    store.ingest(_event("UserPromptSubmit", 4, prompt="duplicate"))
    assert store.snapshot("session-a") == before_duplicate
    store.ingest(_event("SessionStart", 1))
    assert store.snapshot("session-a")["goal"] == "new"
    assert store.snapshot("session-a")["session_started"] is True
    assert store.snapshot("session-a")["gaps"] == {
        "highest_seen": 4,
        "missing_count": 1,
        "missing_ranges": [(3, 3)],
        "untracked_through": 0,
    }


def test_projection_is_bounded_redacted_and_session_local() -> None:
    """Keep recent context, not raw results, and isolate sessions."""
    store = HookContextStore()
    store.ingest(_event("UserPromptSubmit", 1, prompt="old goal"))
    for sequence in range(2, 60):
        store.ingest(
            _event(
                "PostToolUse",
                sequence,
                tool_name="Read",
                tool_input={"file_path": f"{sequence}.py"},
                tool_response={
                    "content": [
                        {
                            "authorization": "not-for-the-snapshot",
                            "accessKey": "placeholder",
                            "signedUrl": "placeholder",
                        }
                    ],
                    "private_key": "placeholder",
                    "privateKey": "placeholder",
                },
            )
        )
    store.ingest(_event("Stop", 60, last_assistant_message="done"))
    store.ingest(_event("UserPromptSubmit", 1, session_id="session-b", prompt="other"))
    snapshot = store.snapshot("session-a")
    assert snapshot["goal"] == "old goal"
    assert snapshot["last_assistant_message"] == "done"
    assert snapshot["active_files"] == [f"{n}.py" for n in range(52, 60)]
    assert snapshot["last_results"] == [
        {
            "relay_seq": n,
            "tool": "Read",
            "result": (
                '{"content": [{"authorization": "[redacted]", '
                '"accessKey": "[redacted]", "signedUrl": "[redacted]"}], '
                '"private_key": "[redacted]", "privateKey": "[redacted]"}'
            ),
        }
        for n in range(55, 60)
    ]
    assert store.snapshot("session-b")["active_files"] == []


def test_bounded_advance_expires_oldest_gap_and_refuses_expired_arrival() -> None:
    """Events in expired gaps must not be silently accepted or applied."""
    store = HookContextStore()
    for sequence in range(2, 260, 2):
        store.ingest(_event("Notification", sequence))
    before_expired = store.snapshot("session-a")
    assert before_expired["gaps"]["untracked_through"] == 1
    with pytest.raises(ValueError, match="expired gap history"):
        store.ingest(_event("SessionStart", 1))
    assert store.snapshot("session-a") == before_expired
    assert store.snapshot("session-a")["session_started"] is False


def test_bounded_heal_split_expires_oldest_interval() -> None:
    """Splitting the oldest range at capacity expires only its first half."""
    store = HookContextStore()
    for sequence in range(4, 259, 2):
        store.ingest(_event("Notification", sequence))
    store.ingest(_event("Notification", 2))
    assert store.snapshot("session-a")["gaps"] == {
        "highest_seen": 258,
        "missing_count": 128,
        "missing_ranges": [(n, n) for n in range(3, 258, 2)],
        "untracked_through": 1,
    }


def test_failure_survives_unrelated_success_but_not_retry() -> None:
    """A successful retry closes its failure even if the result is empty."""
    store = HookContextStore()
    failed_tool = {"tool_name": "Read", "tool_input": {"file_path": "sample.py"}}
    store.ingest(_event("PostToolUseFailure", 1, **failed_tool, error="missing file"))
    store.ingest(_event("PostToolUse", 2, tool_name="Bash", tool_response="ok"))
    assert store.snapshot("session-a")["open_failure"] == {
        "relay_seq": 1,
        "tool": "Read",
        "result": "missing file",
    }
    store.ingest(_event("PostToolUse", 3, **failed_tool))
    assert store.snapshot("session-a")["open_failure"] is None


def test_pre_tool_use_updates_active_files_without_a_result() -> None:
    """An in-flight tool reports its file but does not invent an outcome."""
    store = HookContextStore()
    store.ingest(
        _event(
            "PreToolUse",
            1,
            tool_name="Read",
            tool_input={"file_path": "in-flight.py"},
        )
    )
    snapshot = store.snapshot("session-a")
    assert snapshot["active_files"] == ["in-flight.py"]
    assert snapshot["last_results"] == []
    assert snapshot["open_failure"] is None


def test_same_tool_with_different_inputs_does_not_close_a_failure() -> None:
    """Only the same canonical tool input can clear a matching failure."""
    store = HookContextStore()
    store.ingest(
        _event(
            "PostToolUseFailure",
            1,
            tool_name="Grep",
            tool_input={"path": ".", "pattern": "first"},
            error="unable to search",
        )
    )
    store.ingest(
        _event(
            "PostToolUse",
            2,
            tool_name="Grep",
            tool_input={"path": ".", "pattern": "second"},
        )
    )
    assert store.snapshot("session-a")["open_failure"] is not None
    store.ingest(
        _event(
            "PostToolUse",
            3,
            tool_name="Grep",
            tool_input={"pattern": "first", "path": "."},
        )
    )
    assert store.snapshot("session-a")["open_failure"] is None


def test_open_failure_outlives_rolling_tool_results() -> None:
    """Unrelated work cannot close a failure when its result ages out."""
    store = HookContextStore()
    target = {"tool_name": "Read", "tool_input": {"file_path": "missing.py"}}
    store.ingest(_event("PostToolUseFailure", 1, **target, error="missing file"))
    for sequence in range(3, 60):
        store.ingest(
            _event(
                "PostToolUse",
                sequence,
                tool_name="Read",
                tool_input={"file_path": f"other-{sequence}.py"},
                tool_response="ok",
            )
        )
    failure = store.snapshot("session-a")["open_failure"]
    assert failure == {"relay_seq": 1, "tool": "Read", "result": "missing file"}
    store.ingest(_event("PostToolUse", 60, **target))
    store.ingest(_event("PostToolUseFailure", 2, **target, error="late"))
    assert store.snapshot("session-a")["open_failure"] is None


def test_daemon_rpc_refuses_ledger_and_shares_store_across_connections() -> None:
    """The production dispatch entries share only live, per-daemon state."""
    with TestClient(build_app()) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "type": "hook_event",
                    "id": "one",
                    "payload": _event("Stop", 2, last_assistant_message="done"),
                }
            )
            assert ws.receive_json() == {"type": "hook_event_accepted", "id": "one"}
            ws.send_json(
                {
                    "type": "hook_event",
                    "id": "ledger",
                    "payload": _event("Stop", 1, recv_seq=1),
                }
            )
            assert ws.receive_json() == {
                "type": "error",
                "id": "ledger",
                "message": "refusing to ingest a hook ledger record",
            }
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {"type": "hook_context", "id": "two", "session_id": "session-a"}
            )
            reply = ws.receive_json()
            assert reply["type"] == "hook_context"
            assert reply["id"] == "two"
            assert reply["snapshot"]["last_assistant_message"] == "done"
            assert reply["snapshot"]["gaps"]["missing_ranges"] == [[1, 1]]


def test_context_cli_passes_session_and_prints_the_snapshot() -> None:
    """The CLI sends the explicit session and prints machine-readable output."""
    snapshot = {"goal": "inspect", "gaps": {"missing_count": 0}}
    with patch(
        "punt_vox.commands.call_context.HookContextClient.snapshot",
        return_value=snapshot,
    ) as query:
        result = CliRunner().invoke(
            CallCli.build_app(), ["context", "--session", "session-a"]
        )
    query.assert_awaited_once_with("session-a")
    assert result.exit_code == 0
    assert json.loads(result.stdout) == snapshot


def test_context_cli_describes_session_without_naming_a_harness() -> None:
    """The session selector is shared by hook sources."""
    result = CliRunner().invoke(CallCli.build_app(), ["context", "--help"])
    assert result.exit_code == 0
    assert "Hook source's session ID." in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply_type", "snapshot", "error"),
    [
        ("hook_context", {"goal": "inspect"}, None),
        ("hook_context", None, "no snapshot object"),
        ("unexpected", None, "unexpected voxd hook context reply"),
    ],
)
async def test_context_client_validates_reply_and_closes_connection(
    reply_type: str, snapshot: object, error: str | None
) -> None:
    """The authenticated transport can return only the requested snapshot."""
    transport = AsyncMock()

    async def reply(request: dict[str, object], *, timeout: int) -> dict[str, object]:
        assert timeout == 3
        assert request["session_id"] == "session-a"
        return {"id": request["id"], "type": reply_type, "snapshot": snapshot}

    transport.send_and_recv.side_effect = reply
    with patch("punt_vox.hook_context_client.VoxdTransport", return_value=transport):
        if error is None:
            assert await HookContextClient.snapshot("session-a") == snapshot
        else:
            with pytest.raises(VoxdProtocolError, match=error):
                await HookContextClient.snapshot("session-a")
    transport.connect.assert_awaited_once()
    transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_event_requires_a_delivery_receipt() -> None:
    """A detached relay succeeds only after its own event is acknowledged."""
    transport = AsyncMock()
    payload = _event("Stop", 1)

    async def reply(request: dict[str, object], *, timeout: int) -> dict[str, object]:
        assert timeout == 3
        assert request["payload"] == payload
        return {"id": request["id"], "type": "hook_event_accepted"}

    transport.send_and_recv.side_effect = reply
    with patch("punt_vox.hook_context_client.VoxdTransport", return_value=transport):
        await HookContextClient.send_event(payload)
    transport.close.assert_awaited_once()


def test_context_cli_reports_a_missing_snapshot() -> None:
    """A rejected query cannot be mistaken for an empty, successful result."""
    with patch(
        "punt_vox.commands.call_context.HookContextClient.snapshot",
        side_effect=VoxdProtocolError("no context"),
    ):
        result = CliRunner().invoke(
            CallCli.build_app(), ["context", "--session", "session-a"]
        )
    assert result.exit_code == 1
    assert "Hook context unavailable: no context" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("rpc", "message"),
    [
        (
            {"type": "hook_event", "payload": []},
            "hook_event requires one hook payload object",
        ),
        (
            {"type": "hook_context", "session_id": "unknown"},
            "no hook context for that session",
        ),
        (
            {"type": "hook_event", "payload": _event("Stop", 1, relay_seq=True)},
            "hook payload requires a positive integer relay_seq",
        ),
        (
            {"type": "hook_event", "payload": _event("Stop", 1, session_id="")},
            "hook payload requires a valid session_id",
        ),
        (
            {"type": "hook_event", "payload": _event("", 1)},
            "hook payload requires a valid hook_event_name",
        ),
    ],
)
def test_daemon_rpc_rejects_invalid_hook_requests(
    rpc: dict[str, object], message: str
) -> None:
    """Invalid events and absent sessions produce request-scoped errors."""
    with (
        TestClient(build_app()) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_json({**rpc, "id": "rejected"})
        assert ws.receive_json() == {
            "type": "error",
            "id": "rejected",
            "message": message,
        }


def test_context_store_evicts_least_recently_active_session() -> None:
    """Only ingest refreshes a session's place in the bounded store."""
    store = HookContextStore()
    for n in range(64):
        store.ingest(_event("Notification", 1, session_id=str(n)))
    store.ingest(_event("SessionStart", 2, session_id="0"))
    store.ingest(_event("Notification", 1, session_id="64"))
    assert store.snapshot("0")["session_started"] is True
    with pytest.raises(ValueError, match="no hook context"):
        store.snapshot("1")


def test_dev_plugin_forwards_without_blocking() -> None:
    """Each registered hook event has exactly one unrestricted relay."""
    path = Path(__file__).resolve().parents[1] / "plugin/hooks/hooks.json"
    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    assert {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "Stop",
        "Notification",
        "PreCompact",
        "SubagentStart",
        "SubagentStop",
        "SessionEnd",
    } <= hooks.keys()
    for event, groups in hooks.items():
        assert [
            (hook, "matcher" in group)
            for group in groups
            for hook in group["hooks"]
            if hook["command"].endswith("/context-event.sh")
        ] == [
            (
                {
                    "type": "command",
                    "command": "${CLAUDE_PLUGIN_ROOT}/hooks/context-event.sh",
                    "async": True,
                },
                False,
            )
        ], event


@pytest.mark.parametrize(
    "event",
    ["PreCompact", "SubagentStart", "SubagentStop", "SessionEnd", "Notification"],
)
def test_progress_only_plugin_events_advance_gaps_without_changing_projection(
    event: str,
) -> None:
    """These events signal live progress without contributing curated fields."""
    store = HookContextStore()
    store.ingest(_event("UserPromptSubmit", 1, prompt="keep this goal"))
    before = store.snapshot("session-a")
    store.ingest(_event(event, 3, prompt="must not replace the goal"))
    assert store.snapshot("session-a") == {
        **before,
        "gaps": {
            "highest_seen": 3,
            "missing_ranges": [(2, 2)],
            "missing_count": 1,
            "untracked_through": 0,
        },
    }
