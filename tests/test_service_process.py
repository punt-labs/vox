"""Tests for punt_vox.service.process — process management."""
# pyright: reportUnknownLambdaType=false

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service import DEFAULT_PORT
from punt_vox.service.backend import PortProbeError
from punt_vox.service.installer import ServiceInstaller
from punt_vox.service.port_file_cleanup import PortFileCleanup
from punt_vox.service.process import ProcessManager
from punt_vox.service.termination import Termination

# ---------------------------------------------------------------------------
# Exec args
# ---------------------------------------------------------------------------


def test_voxd_exec_args() -> None:
    args = ServiceInstaller._voxd_exec_args()
    assert args[0].endswith("voxd")
    assert "--port" in args
    assert str(DEFAULT_PORT) in args


def test_voxd_exec_args_resolves_relative_to_sys_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_voxd_exec_args must use sys.executable/../voxd, not $PATH."""
    bin_dir = tmp_path / "fake-dist" / "bin"
    bin_dir.mkdir(parents=True)
    fake_voxd = bin_dir / "voxd"
    fake_voxd.write_text("#!/bin/sh\necho fake voxd")
    fake_voxd.chmod(0o755)
    fake_python = bin_dir / "python"
    fake_python.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"')
    fake_python.chmod(0o755)

    monkeypatch.setattr("punt_vox.service.installer.sys.executable", str(fake_python))
    args = ServiceInstaller._voxd_exec_args()
    assert args[0] == str(fake_voxd)
    assert "--port" in args


def test_voxd_exec_args_ignores_stale_voxd_on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale voxd earlier on $PATH must not be baked into ExecStart."""
    current = tmp_path / "current" / "bin"
    current.mkdir(parents=True)
    (current / "voxd").write_text("current")
    (current / "voxd").chmod(0o755)
    (current / "python").write_text("#!/bin/sh\n")
    (current / "python").chmod(0o755)

    stale_dir = tmp_path / "stale" / "bin"
    stale_dir.mkdir(parents=True)
    (stale_dir / "voxd").write_text("stale")
    (stale_dir / "voxd").chmod(0o755)

    monkeypatch.setenv("PATH", f"{stale_dir}:{current}:/usr/bin:/bin")
    monkeypatch.setattr(
        "punt_vox.service.installer.sys.executable",
        str(current / "python"),
    )

    args = ServiceInstaller._voxd_exec_args()
    assert args[0] == str(current / "voxd"), (
        "Expected sys.executable-relative voxd, "
        f"got {args[0]} — stale binary from PATH leaked in."
    )


def test_voxd_exec_args_missing_binary_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing voxd next to sys.executable must raise SystemExit."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)

    monkeypatch.setattr("punt_vox.service.installer.sys.executable", str(fake_python))
    with pytest.raises(SystemExit, match="voxd binary not found"):
        ServiceInstaller._voxd_exec_args()


def test_voxd_exec_args_rejects_non_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A voxd file without the executable bit must raise SystemExit."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    voxd = bin_dir / "voxd"
    voxd.write_text("#!/bin/sh\n")
    voxd.chmod(0o644)

    monkeypatch.setattr("punt_vox.service.installer.sys.executable", str(fake_python))
    with pytest.raises(SystemExit, match="not executable"):
        ServiceInstaller._voxd_exec_args()


def test_voxd_exec_args_rejects_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory at the voxd path must raise SystemExit, not succeed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    (bin_dir / "voxd").mkdir()

    monkeypatch.setattr("punt_vox.service.installer.sys.executable", str(fake_python))
    with pytest.raises(SystemExit, match="voxd binary not found"):
        ServiceInstaller._voxd_exec_args()


# ---------------------------------------------------------------------------
# find_pid_on_port -- fails closed
# ---------------------------------------------------------------------------


def _ran(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


@patch("punt_vox.service.process.platform.system", return_value="Darwin")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_on_port_macos(mock_run: MagicMock, _mock_sys: MagicMock) -> None:
    mgr = ProcessManager()
    mock_run.return_value = _ran(0, "12345\n")
    assert mgr.find_pid_on_port(8421) == [12345]
    assert mock_run.call_args.kwargs["timeout"] == 5


@patch("punt_vox.service.process.platform.system", return_value="Darwin")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_on_port_macos_multiple(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    mgr = ProcessManager()
    mock_run.return_value = _ran(0, "12345\n67890\n")
    assert mgr.find_pid_on_port(8421) == [12345, 67890]


@patch("punt_vox.service.process.platform.system", return_value="Darwin")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_on_port_ignores_non_decimal_digits(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    """Superscript digits pass str.isdigit but break int(); isdecimal drops them."""
    mock_run.return_value = _ran(0, "123\n²\n456\n")
    assert ProcessManager().find_pid_on_port(8421) == [123, 456]


@patch("punt_vox.service.process.platform.system", return_value="Linux")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_on_port_linux(mock_run: MagicMock, _mock_sys: MagicMock) -> None:
    mgr = ProcessManager()
    mock_run.return_value = _ran(0, "8421/tcp:  6789\n")
    assert mgr.find_pid_on_port(8421) == [6789]
    assert mock_run.call_args.args[0] == ["fuser", "8421/tcp"]


@patch("punt_vox.service.process.platform.system", return_value="Linux")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_on_port_exit_1_empty_is_empty(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    """The tool ran cleanly and found nothing: the port is genuinely empty."""
    mock_run.return_value = _ran(1)
    assert ProcessManager().find_pid_on_port(8421) == []


@patch("punt_vox.service.process.platform.system", return_value="Linux")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_on_port_error_exit_raises(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    """An error exit is a broken probe, never an empty port."""
    mock_run.return_value = _ran(2, stderr="fuser: bad option")
    with pytest.raises(PortProbeError, match="fuser exited 2"):
        ProcessManager().find_pid_on_port(8421)


@patch("punt_vox.service.process.platform.system", return_value="Darwin")
@patch(
    "punt_vox.service.process.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="lsof", timeout=5),
)
def test_find_pid_on_port_timeout_raises(
    _mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    """A stalled tool raises -- it cannot read as an empty port."""
    with pytest.raises(PortProbeError, match="lsof"):
        ProcessManager().find_pid_on_port(8421)


@patch("punt_vox.service.process.platform.system", return_value="Linux")
@patch(
    "punt_vox.service.process.subprocess.run",
    side_effect=FileNotFoundError(2, "No such file or directory"),
)
def test_find_pid_on_port_fuser_missing_raises_naming_psmisc(
    _mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    """A missing fuser raises naming the tool and the package that ships it."""
    with pytest.raises(PortProbeError, match=r"fuser: not found \(install psmisc\)"):
        ProcessManager().find_pid_on_port(8421)


# ---------------------------------------------------------------------------
# identity / is_vox_daemon_process -- fails closed
# ---------------------------------------------------------------------------

_LSTART = "Tue Nov  4 15:53:01 2026"


@patch("punt_vox.service.process.subprocess.run")
def test_identity_parses_start_time_and_command(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(0, f"{_LSTART} /opt/bin/voxd --port 8421\n")
    seen = ProcessManager().identity(123)
    assert seen is not None
    assert seen.started == _LSTART
    assert seen.command == "/opt/bin/voxd --port 8421"


@patch("punt_vox.service.process.subprocess.run")
def test_identity_gone_pid_is_none(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(1)
    assert ProcessManager().identity(123) is None


@patch("punt_vox.service.process.subprocess.run")
def test_identity_ps_failure_raises(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(2, stderr="ps: illegal option")
    with pytest.raises(PortProbeError, match="ps exited 2"):
        ProcessManager().identity(123)


@patch("punt_vox.service.process.subprocess.run")
def test_is_vox_daemon_process_true(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(0, f"{_LSTART} /usr/local/bin/voxd --port 8421")
    assert ProcessManager().is_vox_daemon_process(123) is True


@patch("punt_vox.service.process.subprocess.run")
def test_is_vox_daemon_process_python_launched_voxd(mock_run: MagicMock) -> None:
    """A voxd run through its interpreter still names voxd on the command line."""
    cmd = "/home/u/.local/share/uv/tools/punt-vox/bin/python /home/u/.local/bin/voxd"
    mock_run.return_value = _ran(0, f"{_LSTART} {cmd}")
    assert ProcessManager().is_vox_daemon_process(123) is True


@patch("punt_vox.service.process.subprocess.run")
def test_is_vox_daemon_process_retired_serve_form_is_not_voxd(
    mock_run: MagicMock,
) -> None:
    """The retired ``vox serve`` command line no longer counts as the daemon."""
    mock_run.return_value = _ran(0, f"{_LSTART} /Users/someone/.local/bin/vox serve")
    assert ProcessManager().is_vox_daemon_process(123) is False


@patch("punt_vox.service.process.subprocess.run")
def test_is_vox_daemon_process_false(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(0, f"{_LSTART} nginx: master process")
    assert ProcessManager().is_vox_daemon_process(123) is False


@patch(
    "punt_vox.service.process.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="ps", timeout=5),
)
def test_is_vox_daemon_process_timeout_raises(_mock_run: MagicMock) -> None:
    """A ps timeout raises rather than reading as "not voxd"."""
    with pytest.raises(PortProbeError, match="ps"):
        ProcessManager().is_vox_daemon_process(123)


@patch("punt_vox.service.process.subprocess.run")
def test_is_vox_daemon_process_ps_failure_raises(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(2)
    with pytest.raises(PortProbeError):
        ProcessManager().is_vox_daemon_process(123)


# ---------------------------------------------------------------------------
# kill_pid -- through a pinned handle
# ---------------------------------------------------------------------------


def test_kill_pid_terminates_through_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = MagicMock()
    handle.terminate.return_value = Termination.EXITED
    monkeypatch.setattr(ProcessManager, "open_voxd", lambda self, pid: handle)
    assert ProcessManager().kill_pid(100) is True
    handle.terminate.assert_called_once_with(7.0)


def test_kill_pid_gone_or_not_voxd_signals_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ProcessManager, "open_voxd", lambda self, pid: None)
    assert ProcessManager().kill_pid(100) is True


def test_kill_pid_survivor_reports_false(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = MagicMock()
    handle.terminate.return_value = Termination.SURVIVED
    monkeypatch.setattr(ProcessManager, "open_voxd", lambda self, pid: handle)
    assert ProcessManager().kill_pid(100) is False


# ---------------------------------------------------------------------------
# kill_stale_daemon
# ---------------------------------------------------------------------------


@patch.object(ProcessManager, "remove_port_file")
@patch.object(ProcessManager, "kill_pid", return_value=True)
@patch.object(ProcessManager, "is_vox_daemon_process", return_value=True)
@patch.object(ProcessManager, "find_pid_on_port", return_value=[999])
@patch.object(ProcessManager, "read_port_file", return_value=8421)
def test_kill_stale_daemon_kills_process(
    _mock_port: MagicMock,
    mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    mock_remove: MagicMock,
) -> None:
    mgr = ProcessManager()
    assert mgr.kill_stale_daemon() is True
    mock_find.assert_called_once_with(8421)
    mock_kill.assert_called_once_with(999)
    mock_remove.assert_called_once()


@patch.object(ProcessManager, "find_pid_on_port", return_value=[])
@patch.object(ProcessManager, "read_port_file", return_value=None)
def test_kill_stale_daemon_no_process(
    _mock_port: MagicMock, _mock_find: MagicMock
) -> None:
    mgr = ProcessManager()
    assert mgr.kill_stale_daemon() is False


@patch.object(ProcessManager, "remove_port_file")
@patch.object(ProcessManager, "kill_pid", return_value=True)
@patch.object(ProcessManager, "is_vox_daemon_process", return_value=True)
@patch.object(ProcessManager, "find_pid_on_port", return_value=[555])
@patch.object(ProcessManager, "read_port_file", return_value=None)
def test_kill_stale_daemon_uses_default_port(
    _mock_port: MagicMock,
    mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    _mock_remove: MagicMock,
) -> None:
    mgr = ProcessManager()
    assert mgr.kill_stale_daemon() is True
    mock_find.assert_called_once_with(DEFAULT_PORT)


@patch.object(ProcessManager, "kill_pid")
@patch.object(ProcessManager, "is_vox_daemon_process", return_value=False)
@patch.object(ProcessManager, "find_pid_on_port", return_value=[999])
@patch.object(ProcessManager, "read_port_file", return_value=8421)
def test_kill_stale_daemon_skips_non_vox_process(
    _mock_port: MagicMock,
    _mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
) -> None:
    mgr = ProcessManager()
    assert mgr.kill_stale_daemon() is False
    mock_kill.assert_not_called()


@patch.object(ProcessManager, "remove_port_file")
@patch.object(ProcessManager, "kill_pid", return_value=True)
@patch.object(ProcessManager, "is_vox_daemon_process", side_effect=[False, True])
@patch.object(ProcessManager, "find_pid_on_port", return_value=[100, 200])
@patch.object(ProcessManager, "read_port_file", return_value=8421)
def test_kill_stale_daemon_iterates_pids(
    _mock_port: MagicMock,
    _mock_find: MagicMock,
    mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    mock_remove: MagicMock,
) -> None:
    """First PID is a client (not vox), second is the daemon -- kills second."""
    mgr = ProcessManager()
    assert mgr.kill_stale_daemon() is True
    assert mock_is_vox.call_count == 2
    mock_kill.assert_called_once_with(200)
    mock_remove.assert_called_once()


@patch.object(ProcessManager, "remove_port_file")
@patch.object(ProcessManager, "kill_pid", return_value=False)
@patch.object(ProcessManager, "is_vox_daemon_process", return_value=True)
@patch.object(ProcessManager, "find_pid_on_port", return_value=[999])
@patch.object(ProcessManager, "read_port_file", return_value=8421)
def test_kill_stale_daemon_no_cleanup_on_kill_failure(
    _mock_port: MagicMock,
    _mock_find: MagicMock,
    _mock_is_vox: MagicMock,
    mock_kill: MagicMock,
    mock_remove: MagicMock,
) -> None:
    """When kill_pid fails, state files are NOT removed."""
    mgr = ProcessManager()
    assert mgr.kill_stale_daemon() is False
    mock_kill.assert_called_once_with(999)
    mock_remove.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_port_free
# ---------------------------------------------------------------------------


@patch.object(ProcessManager, "find_pid_on_port", return_value=[1234])
@patch.object(ProcessManager, "kill_stale_daemon", return_value=False)
def test_ensure_port_free_raises_when_occupied(
    _mock_kill: MagicMock,
    _mock_find: MagicMock,
) -> None:
    """ensure_port_free raises SystemExit when port is still occupied after kill."""
    with pytest.raises(SystemExit, match="still in use"):
        ProcessManager().ensure_port_free()


@patch.object(ProcessManager, "find_pid_on_port", return_value=[])
@patch.object(ProcessManager, "kill_stale_daemon", return_value=False)
def test_ensure_port_free_succeeds_when_clear(
    _mock_kill: MagicMock,
    _mock_find: MagicMock,
) -> None:
    """ensure_port_free succeeds when no PIDs on port after kill."""
    ProcessManager().ensure_port_free()  # Should not raise


def test_ensure_port_free_uses_port_file_not_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-kill port re-check targets the port file, not ``DEFAULT_PORT``."""
    captured: list[int] = []

    def fake_find(self: ProcessManager, port: int) -> list[int]:
        captured.append(port)
        return []

    monkeypatch.setattr(ProcessManager, "read_port_file", lambda self: 9999)
    monkeypatch.setattr(ProcessManager, "kill_stale_daemon", lambda self: True)
    monkeypatch.setattr(ProcessManager, "find_pid_on_port", fake_find)

    ProcessManager().ensure_port_free()

    assert captured == [9999], (
        f"ensure_port_free checked the wrong port: expected [9999], got {captured}"
    )


def test_ensure_port_free_error_message_reports_actual_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SystemExit message must name the ACTUAL port, not DEFAULT_PORT."""
    monkeypatch.setattr(ProcessManager, "read_port_file", lambda self: 9999)
    monkeypatch.setattr(ProcessManager, "kill_stale_daemon", lambda self: False)
    monkeypatch.setattr(ProcessManager, "find_pid_on_port", lambda self, port: [5555])

    with pytest.raises(SystemExit, match="Port 9999 is still in use"):
        ProcessManager().ensure_port_free()


def test_ensure_port_free_falls_back_to_default_when_port_file_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No port file on disk: fall back to ``DEFAULT_PORT`` for the re-check."""
    captured: list[int] = []

    def fake_find(self: ProcessManager, port: int) -> list[int]:
        captured.append(port)
        return []

    monkeypatch.setattr(ProcessManager, "read_port_file", lambda self: None)
    monkeypatch.setattr(ProcessManager, "kill_stale_daemon", lambda self: False)
    monkeypatch.setattr(ProcessManager, "find_pid_on_port", fake_find)

    ProcessManager().ensure_port_free()

    assert captured == [DEFAULT_PORT]


def test_ensure_port_free_reads_port_file_before_kill_stale_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Port file read MUST happen BEFORE ``kill_stale_daemon`` runs.

    A successful kill removes the port file, so reading it afterwards would
    re-check the default port instead of the one voxd last bound.
    """
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    port_file = tmp_path / "serve.port"
    port_file.write_text("9999\n", encoding="utf-8")

    kill_called: list[bool] = []

    def spy_kill(self: ProcessManager) -> bool:
        kill_called.append(True)
        # Simulate the real kill_stale_daemon happy-path: port file removed.
        self.remove_port_file()
        return True

    captured_port: list[int] = []

    def spy_find(self: ProcessManager, port: int) -> list[int]:
        captured_port.append(port)
        return []

    monkeypatch.setattr(ProcessManager, "kill_stale_daemon", spy_kill)
    monkeypatch.setattr(ProcessManager, "find_pid_on_port", spy_find)

    ProcessManager().ensure_port_free()

    assert kill_called == [True]
    assert captured_port == [9999], (
        f"find_pid_on_port was called with {captured_port}, not [9999]."
    )
    assert DEFAULT_PORT not in captured_port


# ---------------------------------------------------------------------------
# Fail closed on stderr, listeners only, port file errors surface
# ---------------------------------------------------------------------------


@patch("punt_vox.service.process.platform.system", return_value="Linux")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_exit_1_with_stderr_raises(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    """Exit 1 is "found nothing" only when the tool also said nothing."""
    mock_run.return_value = _ran(1, stderr="fuser: permission denied")
    with pytest.raises(PortProbeError, match="permission denied"):
        ProcessManager().find_pid_on_port(8421)


@patch("punt_vox.service.process.subprocess.run")
def test_identity_exit_1_with_stderr_raises(mock_run: MagicMock) -> None:
    """A ps error exiting 1 must not read as "gone"."""
    mock_run.return_value = _ran(1, stderr="ps: fatal error")
    with pytest.raises(PortProbeError, match="fatal error"):
        ProcessManager().identity(123)


@patch("punt_vox.service.process.subprocess.run")
def test_identity_unparseable_ps_line_is_port_probe_error(mock_run: MagicMock) -> None:
    """A malformed ps line fails the probe, never escapes as ValueError."""
    mock_run.return_value = _ran(0, "garbage")
    with pytest.raises(PortProbeError, match="unparseable"):
        ProcessManager().identity(123)


@patch("punt_vox.service.process.platform.system", return_value="Darwin")
@patch("punt_vox.service.process.subprocess.run")
def test_find_pid_on_port_macos_lists_listeners_only(
    mock_run: MagicMock, _mock_sys: MagicMock
) -> None:
    """lsof matches only the TCP listener, not clients whose peer is 8421."""
    mock_run.return_value = _ran(0, "12345\n")
    ProcessManager().find_pid_on_port(8421)
    assert mock_run.call_args.args[0] == [
        "lsof",
        "-w",
        "-nP",
        "-t",
        "-iTCP:8421",
        "-sTCP:LISTEN",
    ]


def test_read_port_file_missing_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    assert ProcessManager().read_port_file() is None


@pytest.mark.parametrize("content", ["", "84", "not-a-port"])
def test_read_port_file_unparseable_is_a_stale_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    content: str,
) -> None:
    """A partial serve.port (a crash leftover) warns and reads as absent."""
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "serve.port").write_text(content)
    with caplog.at_level("WARNING", logger="punt_vox.service.process"):
        port = ProcessManager().read_port_file()
    if content == "84":
        assert port == 84
        return
    assert port is None
    assert any("serve.port" in r.getMessage() for r in caplog.records)


def test_read_port_file_read_error_is_surfaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read error other than not-found still fails loudly."""
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "serve.port").mkdir()
    with pytest.raises(PortProbeError, match=r"serve\.port"):
        ProcessManager().read_port_file()


def test_remove_port_file_failure_does_not_claim_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed unlink is warned about, never followed by "Removed"."""
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", refuse)
    with caplog.at_level("INFO", logger="punt_vox.service.process"):
        ProcessManager().remove_port_file()
    messages = [r.getMessage() for r in caplog.records]
    assert any("Could not remove" in m for m in messages)
    assert not any(m.startswith("Removed") for m in messages)


@pytest.mark.parametrize(
    ("content", "kept", "outcome"),
    [
        ("8421", False, PortFileCleanup.REMOVED),
        ("", False, PortFileCleanup.REMOVED),
        ("84x", False, PortFileCleanup.REMOVED),
        ("9000", True, PortFileCleanup.OTHER_PORT),
    ],
)
def test_discard_port_file_removes_the_port_or_a_stale_leftover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    kept: bool,
    outcome: PortFileCleanup,
) -> None:
    """A file naming the port, or one that does not parse, is removed."""
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "serve.port").write_text(content)
    assert ProcessManager().discard_port_file(8421) is outcome
    assert (tmp_path / "serve.port").exists() is kept


def test_discard_port_file_without_a_file_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    assert ProcessManager().discard_port_file(8421) is PortFileCleanup.ABSENT
    assert not (tmp_path / "serve.port").exists()


def test_discard_port_file_unlink_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removal that fails is reported to the caller, not only logged."""
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "serve.port").write_text("8421")

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(PortProbeError, match="read-only"):
        ProcessManager().discard_port_file(8421)


def test_discard_port_file_read_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "serve.port").mkdir()
    with pytest.raises(PortProbeError, match=r"serve\.port"):
        ProcessManager().discard_port_file(8421)
