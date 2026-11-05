"""Tests for punt_vox.service.systemd -- the Linux systemd backend.

``PrivilegedRun`` is driven by ``FakeSudo`` (scripted exit codes per argv) and
a scripted terminal check, so every sudoers policy -- scoped NOPASSWD, mixed
NOPASSWD, password with and without a terminal -- runs here with no real sudo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from _lifecycle_fakes import FakeSudo

from punt_vox.service.backend import BackendCommandError, PrivilegeDeniedError
from punt_vox.service.systemd import PrivilegedRun, SystemdBackend

_N_STOP = ("sudo", "-n", "systemctl", "stop", "voxd")
_I_STOP = ("sudo", "systemctl", "stop", "voxd")
_NEEDS_PASSWORD = "sudo: a password is required"


def _run(
    script: dict[tuple[str, ...], list[tuple[int, str]]], *, tty: bool
) -> tuple[PrivilegedRun, FakeSudo]:
    sudo = FakeSudo(script)
    return PrivilegedRun(sudo, lambda: tty), sudo


def _argvs(sudo: FakeSudo) -> list[tuple[str, ...]]:
    return [argv for argv, _ in sudo.calls]


# ---------------------------------------------------------------------------
# PrivilegedRun -- sudo -n first, never a privilege probe
# ---------------------------------------------------------------------------


def test_scoped_nopasswd_runs_once_no_probe_no_prompt() -> None:
    """``sudo -n`` succeeds: exactly that one call, no -v, no -l, no prompt."""
    run, sudo = _run({_N_STOP: [(0, "")]}, tty=True)
    run("stop")
    assert sudo.calls == [(_N_STOP, True)]
    assert not any("-v" in a or "-l" in a for a in _argvs(sudo))


def test_exit4_permission_denied() -> None:
    run, sudo = _run({_N_STOP: [(4, "Access denied")]}, tty=True)
    with pytest.raises(PrivilegeDeniedError, match="access denied"):
        run("stop")
    assert _argvs(sudo) == [_N_STOP]


def test_exit5_not_loaded_backend_failed() -> None:
    run, sudo = _run({_N_STOP: [(5, "Unit voxd.service not loaded.")]}, tty=True)
    with pytest.raises(BackendCommandError, match="vox daemon install") as info:
        run("stop")
    assert info.value.code == 5
    assert _argvs(sudo) == [_N_STOP]


def test_exit1_no_tty_permission_denied_no_prompt() -> None:
    """No terminal: refuse with sudo's stderr verbatim; never prompt or probe."""
    run, sudo = _run({_N_STOP: [(1, _NEEDS_PASSWORD)]}, tty=False)
    with pytest.raises(PrivilegeDeniedError) as info:
        run("stop")
    assert _NEEDS_PASSWORD in str(info.value)
    assert "run `vox daemon stop` from a terminal" in str(info.value)
    assert _argvs(sudo) == [_N_STOP]


@pytest.mark.parametrize(
    ("code", "raised"),
    [(0, None), (4, PrivilegeDeniedError), (1, BackendCommandError)],
)
def test_exit1_tty_interactive_once(code: int, raised: type[Exception] | None) -> None:
    """A terminal: exactly one interactive call follows the -n call.

    Its exit 0 returns; its exit 4 is a refusal; any other non-zero exit is a
    ``BackendCommandError`` the lifecycle answers by observing again.
    """
    run, sudo = _run({_N_STOP: [(1, _NEEDS_PASSWORD)], _I_STOP: [(code, "")]}, tty=True)
    if raised is None:
        run("stop")
    else:
        with pytest.raises(raised):
            run("stop")
    assert sudo.calls == [(_N_STOP, True), (_I_STOP, False)]


def test_mixed_nopasswd_still_prompts() -> None:
    """One unrelated NOPASSWD entry plus a password ``ALL``: sudo -n exits 1.

    A ``sudo -n -l`` probe would answer "permitted" here and skip the prompt
    the command still needs; with no probe, the interactive call is made.
    """
    run, sudo = _run({_N_STOP: [(1, _NEEDS_PASSWORD)], _I_STOP: [(0, "")]}, tty=True)
    run("stop")
    assert _argvs(sudo) == [_N_STOP, _I_STOP]
    assert not any("-l" in argv for argv in _argvs(sudo))


def test_never_reads_stderr_text() -> None:
    """The same exit code with different stderr gives the same classification."""
    outcomes = []
    for stderr in (_NEEDS_PASSWORD, "sudo: Ein Passwort ist notwendig", ""):
        run, _ = _run({_N_STOP: [(1, stderr)]}, tty=False)
        with pytest.raises(PrivilegeDeniedError) as info:
            run("stop")
        outcomes.append(type(info.value))
    assert len(set(outcomes)) == 1


def test_unexpected_exit_is_backend_command_error() -> None:
    run, _ = _run({_N_STOP: [(3, "weird")]}, tty=True)
    with pytest.raises(BackendCommandError, match="exited 3"):
        run("stop")


# ---------------------------------------------------------------------------
# SystemdBackend -- the lifecycle seam
# ---------------------------------------------------------------------------


def _backend(sudo: FakeSudo | None = None) -> SystemdBackend:
    return SystemdBackend(
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
        PrivilegedRun(sudo or FakeSudo({}), lambda: False),
    )


def _ran(code: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=code, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    ("state", "active"),
    [
        ("active", True),
        ("activating", True),
        ("reloading", True),
        ("deactivating", True),
        ("inactive", False),
        ("failed", False),
    ],
)
@patch("punt_vox.service.systemd.subprocess.run")
def test_is_active_counts_a_stopping_unit(
    mock_run: MagicMock, state: str, active: bool
) -> None:
    """A deactivating unit still has a main process: it has not stopped."""
    mock_run.return_value = _ran(0 if state == "active" else 3, f"{state}\n")
    assert _backend().is_active() is active


@pytest.mark.parametrize(("raw", "pid"), [("4242\n", 4242), ("0\n", None)])
@patch("punt_vox.service.systemd.subprocess.run")
def test_main_pid_zero_is_none(mock_run: MagicMock, raw: str, pid: int | None) -> None:
    mock_run.return_value = _ran(0, raw)
    assert _backend().main_pid() == pid


@patch("punt_vox.service.systemd.subprocess.run")
def test_main_pid_failure_raises(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(1, "", "Failed to connect to bus")
    with pytest.raises(BackendCommandError, match="Failed to connect to bus"):
        _backend().main_pid()


def test_request_stop_and_start_go_through_privileged_run() -> None:
    sudo = FakeSudo(
        {_N_STOP: [(0, "")], ("sudo", "-n", "systemctl", "start", "voxd"): [(0, "")]}
    )
    be = _backend(sudo)
    be.request_stop()
    be.request_start()
    assert _argvs(sudo) == [_N_STOP, ("sudo", "-n", "systemctl", "start", "voxd")]


@patch("punt_vox.service.systemd._SYSTEMD_UNIT")
def test_is_installed_follows_the_unit_file(mock_unit: MagicMock) -> None:
    mock_unit.exists.return_value = False
    assert _backend().is_installed() is False
    mock_unit.exists.return_value = True
    assert _backend().is_installed() is True


# ---------------------------------------------------------------------------
# _systemd_install
# ---------------------------------------------------------------------------


@patch("punt_vox.service.systemd.subprocess.run")
def test_systemd_install_invokes_expected_sudo_commands(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install issues four sudo subprocess calls in order."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)

    be = SystemdBackend(
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
        PrivilegedRun(FakeSudo({}), lambda: False),
    )
    be.install("jfreeman")

    sudo_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "sudo"]
    assert len(sudo_calls) == 4, (
        f"Expected 4 sudo calls, got {len(sudo_calls)}: {[c[0][0] for c in sudo_calls]}"
    )
    assert sudo_calls[0][0][0][:2] == ["sudo", "install"]
    assert "/etc/systemd/system/voxd.service" in sudo_calls[0][0][0]
    assert sudo_calls[1][0][0] == ["sudo", "systemctl", "daemon-reload"]
    assert sudo_calls[2][0][0] == ["sudo", "systemctl", "enable", "voxd"]
    assert sudo_calls[3][0][0] == ["sudo", "systemctl", "restart", "voxd"]


@patch("punt_vox.service.systemd.subprocess.run")
def test_systemd_install_restarts_already_running_voxd(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: ``restart`` is called unconditionally."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    mock_run.return_value = MagicMock(returncode=0)

    be = SystemdBackend(
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
        PrivilegedRun(FakeSudo({}), lambda: False),
    )
    be.install("jfreeman")

    restart_calls = [
        c
        for c in mock_run.call_args_list
        if c[0][0][:4] == ["sudo", "systemctl", "restart", "voxd"]
    ]
    assert len(restart_calls) == 1


@patch("punt_vox.service.systemd.subprocess.run")
def test_systemd_install_writes_unit_to_user_tmp_first(
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The systemd unit is materialized as a user-owned tmp file first."""
    fake_home = tmp_path / "home" / "jfreeman"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)

    tmp_unit_path = fake_home / ".punt-labs" / "vox" / "voxd.service.tmp"
    observed_during_sudo: list[bool] = []

    def _capture_install(*args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        observed_during_sudo.append(tmp_unit_path.exists())
        return MagicMock(returncode=0)

    mock_run.side_effect = _capture_install

    be = SystemdBackend(
        lambda: ["/usr/local/bin/voxd", "--port", "8421"],
        PrivilegedRun(FakeSudo({}), lambda: False),
    )
    be.install("jfreeman")

    assert observed_during_sudo, "no subprocess.run calls were observed"
    assert observed_during_sudo[0] is True
    assert not tmp_unit_path.exists()


# ---------------------------------------------------------------------------
# round 2: queries fail closed, sudo calls are bounded and typed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "stdout"), [(1, ""), (1, "Failed to connect to bus"), (4, "")]
)
@patch("punt_vox.service.systemd.subprocess.run")
def test_is_active_query_error_raises(
    mock_run: MagicMock, code: int, stdout: str
) -> None:
    """B12: a failed is-active query is an error, never "inactive"."""
    mock_run.return_value = _ran(code, stdout, "bus error")
    with pytest.raises(BackendCommandError, match="is-active"):
        _backend().is_active()


@patch(
    "punt_vox.service.systemd.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=10),
)
def test_hung_systemctl_query_raises(_mock_run: MagicMock) -> None:
    """B14: a hung systemctl is bounded and typed."""
    with pytest.raises(BackendCommandError, match="could not run"):
        _backend().main_pid()


@patch("punt_vox.service.systemd.subprocess.run")
def test_every_systemctl_query_has_a_timeout(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(0, "4242\n")
    _backend().main_pid()
    assert mock_run.call_args.kwargs.get("timeout")


@patch(
    "punt_vox.service.systemd.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="sudo", timeout=60),
)
def test_hung_sudo_is_backend_command_error(_mock_run: MagicMock) -> None:
    """B14: the privileged call is bounded too."""
    with pytest.raises(BackendCommandError, match="could not run"):
        PrivilegedRun.on_this_host()("stop")


@patch("punt_vox.service.systemd.subprocess.run")
@patch("punt_vox.service.systemd._SYSTEMD_UNIT")
def test_uninstall_failure_is_reported(
    mock_unit: MagicMock, mock_run: MagicMock
) -> None:
    """B13: a failing disable or daemon-reload is not silently ignored."""
    mock_unit.exists.return_value = True

    def run(argv: list[str], **kwargs: object) -> MagicMock:
        if "disable" in argv:
            raise subprocess.CalledProcessError(1, argv, stderr="Access denied")
        return _ran(0)

    mock_run.side_effect = run
    with pytest.raises(BackendCommandError, match="disable"):
        _backend().uninstall()


@patch("punt_vox.service.systemd.subprocess.run")
def test_install_failure_is_backend_command_error(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B11: a failing sudo step is typed, so install reports it."""
    fake_home = tmp_path / "home"
    (fake_home / ".punt-labs" / "vox").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    mock_run.side_effect = FileNotFoundError(2, "No such file: 'sudo'")
    with pytest.raises(BackendCommandError, match="sudo"):
        _backend().install("someone")


def test_install_unwritable_state_dir_is_backend_command_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7: the local tmp-unit write failing is typed, so install reports it."""
    monkeypatch.setenv("HOME", str(tmp_path / "missing-home"))
    with pytest.raises(BackendCommandError, match=r"voxd\.service\.tmp"):
        _backend().install("someone")


# -- a query inside a bounded wait never outlives the wait --------------------


@patch("punt_vox.service.systemd.subprocess.run")
def test_is_active_within_bounds_the_query_by_the_budget(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(0, "active\n")
    assert _backend().is_active_within(0.4).holds is True
    assert mock_run.call_args.kwargs["timeout"] == 0.4


@patch(
    "punt_vox.service.systemd.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=0.4),
)
def test_is_active_within_a_query_cut_short_confirms_nothing(
    _mock_run: MagicMock,
) -> None:
    """A query the budget cut short is "not active yet", not a backend failure."""
    answer = _backend().is_active_within(0.4)
    assert answer.holds is False
    assert answer.unanswered == "systemctl is-active voxd"


@patch("punt_vox.service.systemd.subprocess.run")
def test_is_active_within_a_failed_query_still_raises(mock_run: MagicMock) -> None:
    mock_run.return_value = _ran(1, "", "bus error")
    with pytest.raises(BackendCommandError, match="is-active"):
        _backend().is_active_within(0.4)
