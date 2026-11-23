"""Tests for the ``vox call`` CLI verbs and option handling."""

from __future__ import annotations

from collections.abc import Coroutine, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
import typer
import typer.core
from typer.testing import CliRunner

from punt_vox.commands.call import CallCli
from punt_vox.daemon_control import DaemonControl
from punt_vox.voxd.conversation_mode.call_control import CallControl
from punt_vox.voxd.conversation_mode.call_lock import CallLock

# Force a wide terminal so typer's Rich-based help renderer never wraps flag
# names across lines -- CI's headless runner reports a narrow or undefined
# width, and a plain substring assertion against wrapped/truncated output is
# not what these tests mean to check.
runner = CliRunner(env={"COLUMNS": "200"})


def _call_lock_at(tmp_path: Path) -> AbstractContextManager[object]:
    """Patch ``CallLock.for_repo`` to a lock rooted at *tmp_path*."""
    return patch.object(
        CallLock, "for_repo", return_value=CallLock(tmp_path / "call.lock")
    )


def _call_control_at(tmp_path: Path) -> AbstractContextManager[object]:
    """Patch ``CallControl.for_repo`` to a mailbox rooted at *tmp_path*."""
    return patch.object(
        CallControl, "for_repo", return_value=CallControl(tmp_path / "call.control")
    )


def _context_file(tmp_path: Path, text: str = "Topic: the music loop") -> Path:
    path = tmp_path / "context.md"
    path.write_text(text)
    return path


@pytest.fixture
def idle_voxd() -> Iterator[object]:
    """voxd answers that no Mode B call is running."""
    with patch.object(
        DaemonControl, "request", new_callable=AsyncMock, return_value={"state": "idle"}
    ) as request:
        yield request


def test_stop_writes_a_stop_request(tmp_path: Path, idle_voxd: AsyncMock) -> None:
    CallLock(tmp_path / "call.lock").acquire("conversation mode call active")
    with _call_lock_at(tmp_path), _call_control_at(tmp_path):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code == 0
    idle_voxd.assert_not_awaited()
    control = CallControl(tmp_path / "call.control")
    request = control.consume()
    assert request is not None
    assert request.kind == "stop"


def test_stop_refuses_when_no_call_is_active(tmp_path: Path, idle_voxd: object) -> None:
    with _call_lock_at(tmp_path), _call_control_at(tmp_path):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code != 0
    assert CallControl(tmp_path / "call.control").consume() is None


def test_stop_refuses_against_a_stale_lock_with_a_dead_pid(
    tmp_path: Path, idle_voxd: object
) -> None:
    """Regression: a killed `vox call start` leaves a lock file behind with a
    now-dead pid. `vox call stop` against that stale lock must refuse the
    same as "no call is active" -- not succeed and write a stop request
    into a mailbox nobody will ever read, since the process it thinks it is
    stopping is long gone.
    """
    CallLock(tmp_path / "call.lock").acquire("stale call")
    with (
        patch("os.kill", side_effect=ProcessLookupError),
        _call_lock_at(tmp_path),
        _call_control_at(tmp_path),
    ):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code != 0
    assert "no call is active" in result.output
    assert CallControl(tmp_path / "call.control").consume() is None


def test_the_call_group_registers_its_explicit_verbs() -> None:
    """Context is inspectable without starting or transferring a call."""
    group = cast("typer.core.TyperGroup", typer.main.get_command(CallCli.build_app()))
    assert set(group.commands) == {"start", "stop", "status", "context", "teardown"}


def _start_command() -> typer.core.TyperCommand:
    """Return the ``start`` subcommand's underlying Typer/click command.

    Introspecting the registered parameters, rather than parsing rendered
    ``--help`` text, is immune to Typer's Rich-based help renderer wrapping
    or truncating output based on the runner's detected terminal width.
    """
    app_command = typer.main.get_command(CallCli.build_app())
    group = cast("typer.core.TyperGroup", app_command)
    return cast("typer.core.TyperCommand", group.commands["start"])


def test_start_no_longer_requires_the_script_option() -> None:
    """``--script`` is the dev/test opt-in now, not a required flag."""
    script_param = next(p for p in _start_command().params if p.name == "script")
    assert script_param.required is False


def test_start_requires_a_context_snapshot() -> None:
    """The call agent sees only the snapshot, so a call cannot start without one."""
    context_param = next(p for p in _start_command().params if p.name == "context")
    assert context_param.required is True


def test_start_without_context_is_a_usage_error() -> None:
    result = runner.invoke(CallCli.build_app(), ["start"])
    assert result.exit_code == 2


def _close_coro(coro: Coroutine[object, object, object]) -> None:
    """A no-op ``asyncio.run`` stand-in: closes *coro* without running it."""
    coro.close()


def test_start_help_shows_the_trace_turns_flag() -> None:
    param_names = {p.name for p in _start_command().params}
    assert "trace_turns" in param_names


def test_start_help_does_not_collide_with_the_global_verbose_flag() -> None:
    """Regression: a second, same-named --verbose scoped to `call start`
    silently did something completely different from the pre-existing
    global --verbose (raise the client log level) depending on flag
    position -- vox's own OutputFlags convention says position must never
    change meaning. `call start --help` must not offer a bare `-v`/
    `--verbose` at all; only the distinctly-named --trace-turns.
    """
    result = runner.invoke(CallCli.build_app(), ["start", "--help"])
    assert result.exit_code == 0
    assert "--verbose" not in result.stdout
    assert "-v," not in result.stdout


def test_start_with_trace_turns_echoes_the_turn_timer_to_console(
    tmp_path: Path,
) -> None:
    """Regression: --trace-turns must reach configure_turn_timer_logging as
    echo_to_console=True -- the flag that decides whether the turn-latency
    trace also prints live, never whether it reaches vox.log (that's
    unconditional).
    """
    from punt_vox.commands import pi_call_cli as call_module

    with (
        patch.object(call_module, "configure_turn_timer_logging") as mock_configure,
        patch("asyncio.run", side_effect=_close_coro),
    ):
        call_module.PiCallCli().start(_context_file(tmp_path), trace_turns=True)
    mock_configure.assert_called_once_with(echo_to_console=True)


def test_start_without_trace_turns_does_not_echo_the_turn_timer_to_console(
    tmp_path: Path,
) -> None:
    from punt_vox.commands import pi_call_cli as call_module

    with (
        patch.object(call_module, "configure_turn_timer_logging") as mock_configure,
        patch("asyncio.run", side_effect=_close_coro),
    ):
        call_module.PiCallCli().start(_context_file(tmp_path))
    mock_configure.assert_called_once_with(echo_to_console=False)
