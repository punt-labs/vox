"""Tests for punt_vox.service.daemon_process -- pinned voxd process handles."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _lifecycle_fakes import FakeClock

from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import SystemClock
from punt_vox.service.daemon_process import (
    DaemonProcess,
    PidfdProcess,
    StartTimeProcess,
)
from punt_vox.service.process import ProcessManager
from punt_vox.service.process_identity import ProcessIdentity
from punt_vox.service.signal_escalation import SignalEscalation
from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from collections.abc import Iterator

_VOXD = ProcessIdentity(started="Tue Nov  4 15:53:01 2026", command="/bin/voxd")
_REUSED = ProcessIdentity(started="Tue Nov  4 16:00:00 2026", command="/bin/voxd")
_OTHER = ProcessIdentity(started="Tue Nov  4 15:53:01 2026", command="nginx")


class _Identities:
    """A scripted identity reader: each read pops the next answer."""

    def __init__(self, *answers: ProcessIdentity | None) -> None:
        self._answers = list(answers)
        self.reads = 0

    def __call__(self, pid: int) -> ProcessIdentity | None:
        self.reads += 1
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0]


# ---------------------------------------------------------------------------
# PidfdProcess, the Linux handle
# ---------------------------------------------------------------------------


class _Pidfd:
    """Monkeypatched pidfd syscalls: records signals; exit is scripted."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, exits_on: int) -> None:
        self.sent: list[signal.Signals] = []
        self._exits_on = exits_on
        self._r, self._w = os.pipe()
        monkeypatch.setattr("punt_vox.service.daemon_process.sys.platform", "linux")
        monkeypatch.setattr(os, "pidfd_open", self._open, raising=False)
        monkeypatch.setattr(signal, "pidfd_send_signal", self._send, raising=False)

    def _open(self, pid: int, flags: int = 0) -> int:
        return os.dup(self._r)

    def _send(self, fd: int, sig: signal.Signals) -> None:
        self.sent.append(sig)
        if sig == self._exits_on:
            os.write(self._w, b"x")


@pytest.mark.skipif(sys.platform == "win32", reason="os.pipe select semantics")
def test_pidfd_handle_signals_through_the_pidfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGTERM goes through pidfd_send_signal, never os.kill on the bare pid."""
    fake = _Pidfd(monkeypatch, exits_on=signal.SIGTERM)
    monkeypatch.setattr(os, "kill", _forbidden_kill)
    handle = PidfdProcess.open(4242, _Identities(_VOXD))
    assert handle is not None
    assert handle.exited() is False
    assert handle.terminate(7.0) is Termination.EXITED
    assert fake.sent == [signal.SIGTERM]
    assert handle.exited() is True


def test_pidfd_handle_escalates_to_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Pidfd(monkeypatch, exits_on=signal.SIGKILL)
    monkeypatch.setattr(SignalEscalation, "TERM_GRACE_S", 0.01)
    handle = PidfdProcess.open(4242, _Identities(_VOXD))
    assert handle is not None
    assert handle.terminate(7.0) is Termination.EXITED
    assert fake.sent == [signal.SIGTERM, signal.SIGKILL]


def test_pidfd_handle_refuses_cmdline_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pid whose command line is not voxd yields no handle and no signal."""
    fake = _Pidfd(monkeypatch, exits_on=signal.SIGTERM)
    assert PidfdProcess.open(4242, _Identities(_OTHER)) is None
    assert fake.sent == []


def test_pidfd_handle_gone_pid_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("punt_vox.service.daemon_process.sys.platform", "linux")

    def _gone(pid: int, flags: int = 0) -> int:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(os, "pidfd_open", _gone, raising=False)
    assert PidfdProcess.open(4242, _Identities(_VOXD)) is None


def test_pidfd_permission_denied_reports_not_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Pidfd(monkeypatch, exits_on=signal.SIGTERM)

    def _denied(fd: int, sig: signal.Signals) -> None:
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(signal, "pidfd_send_signal", _denied, raising=False)
    handle = PidfdProcess.open(4242, _Identities(_VOXD))
    assert handle is not None
    assert handle.terminate(7.0) is Termination.REFUSED


def test_handle_terminates_a_real_process(tmp_path: Path) -> None:
    """End to end on this host: open the strongest handle, terminate, reap.

    The pidfd handle where this interpreter has ``os.pidfd_open``; the
    start-time handle otherwise (macOS, or a Python built without it).
    """
    voxd = tmp_path / "voxd"
    voxd.symlink_to(sys.executable)
    proc = subprocess.Popen([str(voxd), "-c", "import time; time.sleep(30)"])
    try:
        handle = DaemonProcess(SystemClock(), ProcessManager().identity).open(proc.pid)
        assert handle is not None
        assert handle.terminate(7.0) is Termination.EXITED
        assert handle.exited() is True
    finally:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# StartTimeProcess, the macOS and pre-pidfd handle
# ---------------------------------------------------------------------------


@pytest.fixture()
def kills(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[int, int]]]:
    sent: list[tuple[int, int]] = []

    def record(pid: int, sig: int) -> None:
        sent.append((pid, sig))

    monkeypatch.setattr("punt_vox.service.daemon_process.os.kill", record)
    yield sent


def test_start_time_handle_skips_sigkill_when_start_time_changed(
    kills: list[tuple[int, int]],
) -> None:
    """The pid was reused during the grace: no SIGKILL reaches the successor."""
    # open, SIGTERM re-check, then every later read sees a new start time.
    identities = _Identities(_VOXD, _VOXD, _VOXD, _REUSED)
    handle = StartTimeProcess.open(4242, identities, FakeClock())
    assert handle is not None
    assert handle.terminate(7.0) is Termination.EXITED
    assert kills == [(4242, signal.SIGTERM)]


def test_start_time_handle_exited_true_for_reused_pid() -> None:
    handle = StartTimeProcess.open(4242, _Identities(_VOXD, _REUSED), FakeClock())
    assert handle is not None
    assert handle.exited() is True


def test_start_time_handle_exited_true_when_gone() -> None:
    handle = StartTimeProcess.open(4242, _Identities(_VOXD, None), FakeClock())
    assert handle is not None
    assert handle.exited() is True


def test_start_time_handle_escalates_when_same_process_survives(
    kills: list[tuple[int, int]],
) -> None:
    handle = StartTimeProcess.open(4242, _Identities(_VOXD), FakeClock())
    assert handle is not None
    assert handle.terminate(7.0) is Termination.SURVIVED
    assert kills == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


def test_start_time_handle_refuses_non_voxd(kills: list[tuple[int, int]]) -> None:
    assert StartTimeProcess.open(4242, _Identities(_OTHER), FakeClock()) is None
    assert kills == []


# ---------------------------------------------------------------------------
# DaemonProcess picks the handle
# ---------------------------------------------------------------------------


def test_daemon_process_falls_back_without_pidfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kernel older than 5.3 (ENOSYS) gets the start-time handle."""
    monkeypatch.setattr("punt_vox.service.daemon_process.sys.platform", "linux")

    def _enosys(pid: int, flags: int = 0) -> int:
        raise OSError(errno.ENOSYS, "not implemented")

    monkeypatch.setattr(os, "pidfd_open", _enosys, raising=False)
    handle = DaemonProcess(FakeClock(), _Identities(_VOXD)).open(4242)
    assert isinstance(handle, StartTimeProcess)


def test_daemon_process_falls_back_when_python_lacks_pidfd_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interpreter built without os.pidfd_open gets the start-time handle."""
    monkeypatch.setattr("punt_vox.service.daemon_process.sys.platform", "linux")
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    handle = DaemonProcess(FakeClock(), _Identities(_VOXD)).open(4242)
    assert isinstance(handle, StartTimeProcess)


def test_daemon_process_macos_uses_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("punt_vox.service.daemon_process.sys.platform", "darwin")
    handle = DaemonProcess(FakeClock(), _Identities(_VOXD)).open(4242)
    assert isinstance(handle, StartTimeProcess)


def _forbidden_kill(pid: int, sig: int) -> None:
    msg = f"os.kill({pid}, {sig}) on a bare pid"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Value identity, typed open failure, bounded terminate
# ---------------------------------------------------------------------------


def test_start_time_handles_on_one_process_are_equal() -> None:
    """Two opens of one process compare equal, so a set holds it once."""
    first = StartTimeProcess.open(4242, _Identities(_VOXD), FakeClock())
    again = StartTimeProcess.open(4242, _Identities(_VOXD), FakeClock())
    later = StartTimeProcess.open(4242, _Identities(_REUSED), FakeClock())
    assert first is not None
    assert again is not None
    assert later is not None
    assert first == again
    assert len({first, again}) == 1
    assert first != later


def test_pidfd_handles_on_one_process_are_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Pidfd(monkeypatch, exits_on=signal.SIGTERM)
    first = PidfdProcess.open(4242, _Identities(_VOXD))
    again = PidfdProcess.open(4242, _Identities(_VOXD))
    assert first is not None
    assert again is not None
    assert first == again
    assert len({first, again}) == 1


def test_pidfd_open_failure_is_port_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pidfd_open error other than ENOSYS fails the probe, typed."""
    monkeypatch.setattr("punt_vox.service.daemon_process.sys.platform", "linux")

    def _emfile(pid: int, flags: int = 0) -> int:
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(os, "pidfd_open", _emfile, raising=False)
    with pytest.raises(PortProbeError, match="Too many open files"):
        DaemonProcess(FakeClock(), _Identities(_VOXD)).open(4242)


def test_start_time_terminate_is_bounded_by_the_budget(
    kills: list[tuple[int, int]],
) -> None:
    """A survivor costs at most the budget it was given, not 5 s + 2 s."""
    clock = FakeClock()
    handle = StartTimeProcess.open(4242, _Identities(_VOXD), clock)
    assert handle is not None
    assert handle.terminate(2.0) is Termination.SURVIVED
    assert clock.monotonic() <= 2.2
    assert kills == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


@pytest.mark.parametrize("budget", [0.0, 0.5])
def test_start_time_terminate_below_the_floor_sends_sigterm_only(
    kills: list[tuple[int, int]], budget: float
) -> None:
    """No SIGKILL without a real SIGTERM grace first."""
    handle = StartTimeProcess.open(4242, _Identities(_VOXD), FakeClock())
    assert handle is not None
    assert handle.terminate(budget) is Termination.TERM_ONLY
    assert kills == [(4242, signal.SIGTERM)]


def test_pidfd_terminate_below_the_floor_sends_sigterm_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Pidfd(monkeypatch, exits_on=signal.SIGKILL)
    handle = PidfdProcess.open(4242, _Identities(_VOXD))
    assert handle is not None
    assert handle.terminate(0.0) is Termination.TERM_ONLY
    assert fake.sent == [signal.SIGTERM]


# ---------------------------------------------------------------------------
# a refused signal is its own outcome; any other OSError is a typed failure
# ---------------------------------------------------------------------------


def test_start_time_permission_denied_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signal the kernel refused is reported as refused, not survived."""

    def denied(pid: int, sig: int) -> None:
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr("punt_vox.service.daemon_process.os.kill", denied)
    handle = StartTimeProcess.open(4242, _Identities(_VOXD), FakeClock())
    assert handle is not None
    assert handle.terminate(0.5) is Termination.REFUSED


def test_pidfd_refused_below_the_floor_is_refused_not_term_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Pidfd(monkeypatch, exits_on=signal.SIGTERM)

    def _denied(fd: int, sig: signal.Signals) -> None:
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(signal, "pidfd_send_signal", _denied, raising=False)
    handle = PidfdProcess.open(4242, _Identities(_VOXD))
    assert handle is not None
    assert handle.terminate(0.0) is Termination.REFUSED


def test_pidfd_signal_oserror_is_port_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pidfd_send_signal failure other than ESRCH/EPERM is typed."""
    _Pidfd(monkeypatch, exits_on=signal.SIGTERM)

    def _einval(fd: int, sig: signal.Signals) -> None:
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(signal, "pidfd_send_signal", _einval, raising=False)
    handle = PidfdProcess.open(4242, _Identities(_VOXD))
    assert handle is not None
    with pytest.raises(PortProbeError, match="Invalid argument"):
        handle.terminate(7.0)


def test_pidfd_wait_oserror_is_port_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A select() failure while waiting on the pidfd is typed."""
    _Pidfd(monkeypatch, exits_on=signal.SIGTERM)
    handle = PidfdProcess.open(4242, _Identities(_VOXD))
    assert handle is not None

    def _ebadf(*args: object) -> object:
        raise OSError(errno.EBADF, "Bad file descriptor")

    monkeypatch.setattr("punt_vox.service.daemon_process.select.select", _ebadf)
    with pytest.raises(PortProbeError, match="Bad file descriptor"):
        handle.exited()


def test_start_time_kill_oserror_is_port_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _einval(pid: int, sig: int) -> None:
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr("punt_vox.service.daemon_process.os.kill", _einval)
    handle = StartTimeProcess.open(4242, _Identities(_VOXD), FakeClock())
    assert handle is not None
    with pytest.raises(PortProbeError, match="Invalid argument"):
        handle.terminate(7.0)
