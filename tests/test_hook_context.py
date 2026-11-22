"""Coverage for sender stamping, live ingest, and curated context inspection."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TextIO, cast
from unittest.mock import patch

import pytest
import typer
from starlette.testclient import TestClient
from typer.testing import CliRunner

from punt_vox.commands.call import CallCli
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


def test_relay_stages_without_waiting_for_delivery(tmp_path: Path) -> None:
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
        ).submit()
    assert staged == [
        {
            "session_id": "a",
            "hook_event_name": "Stop",
            "relay_seq": 1,
            "nested": [{"Api-Key": "[redacted]"}],
        }
    ]


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
    """Global plugin registration cannot relay an event from an unenabled repo."""
    (tmp_path / ".git").mkdir()
    command = HookContextCommand(lambda: {"cwd": str(tmp_path)})
    with patch.object(HookContextRelay, "submit") as submit:
        command.run(enabled_only=True)
    submit.assert_not_called()


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


def test_gap_heals_without_overwriting_newer_fields() -> None:
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
    store.ingest(_event("UserPromptSubmit", 4, prompt="duplicate"))
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


def test_expired_gap_refuses_an_arrival_it_can_no_longer_deduplicate() -> None:
    """Events in expired gaps must not be silently accepted or applied."""
    store = HookContextStore()
    for sequence in range(2, 260, 2):
        store.ingest(_event("Notification", sequence))
    assert (
        cast("dict[str, object]", store.snapshot("session-a")["gaps"])[
            "untracked_through"
        ]
        == 1
    )
    with pytest.raises(ValueError, match="expired gap history"):
        store.ingest(_event("SessionStart", 1))
    assert store.snapshot("session-a")["session_started"] is False


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
