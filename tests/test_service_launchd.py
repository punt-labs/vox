"""Tests for punt_vox.service.launchd -- macOS launchd backend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.service import DEFAULT_PORT
from punt_vox.service.backend import BackendCommandError
from punt_vox.service.launchctl import LaunchctlError
from punt_vox.service.launchd import LaunchdBackend


def _backend_with_mock_agent(
    monkeypatch: pytest.MonkeyPatch,
    plist: Path,
) -> tuple[LaunchdBackend, MagicMock]:
    """Build a LaunchdBackend whose composed LaunchctlAgent is a MagicMock.

    launchd.py's contract after the LaunchctlAgent extraction is "author the
    plist, then delegate every launchctl call to the agent" -- including the
    lifecycle verbs (bootout/bootstrap/kickstart) and the status probe. These
    tests verify that delegation; the agent's own subprocess behavior
    (bootout/bootstrap race handling, the list probe) is covered in
    test_service_launchctl.py.
    """
    agent = MagicMock()

    def _make_agent(label: str, plist_path: str) -> MagicMock:
        return agent

    monkeypatch.setattr("punt_vox.service.launchd.LaunchctlAgent", _make_agent)
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_PLIST", plist)
    be = LaunchdBackend(lambda: ["/usr/local/bin/voxd", "--port", "8421"])
    return be, agent


@pytest.fixture()
def backend() -> LaunchdBackend:
    """Create a LaunchdBackend with a stub exec-args function."""
    return LaunchdBackend(lambda: ["/usr/local/bin/voxd", "--port", "8421"])


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------


def test_launchd_plist_contains_label(backend: LaunchdBackend) -> None:
    content = backend.plist_content()
    assert "com.punt-labs.voxd" in content


def test_launchd_plist_contains_args(backend: LaunchdBackend) -> None:
    content = backend.plist_content()
    assert "voxd" in content
    assert str(DEFAULT_PORT) in content


def test_launchd_plist_has_no_file_log_redirect(backend: LaunchdBackend) -> None:
    """The plist must not tee daemon output to a second, unprotected log file.

    voxd logs once to the 0600 ``vox.log`` via its private file handler.
    ``StandardErrorPath`` would have launchd capture a duplicate, world-readable
    copy of the same records, defeating that file's private permissions.
    """
    content = backend.plist_content()
    assert "StandardOutPath" not in content
    assert "StandardErrorPath" not in content
    assert "voxd-stdout.log" not in content
    assert "voxd-stderr.log" not in content


def test_launchd_plist_keepalive(backend: LaunchdBackend) -> None:
    content = backend.plist_content()
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content


def test_launchd_plist_has_process_type_interactive(backend: LaunchdBackend) -> None:
    """ProcessType=Interactive prevents App Nap throttling."""
    content = backend.plist_content()
    assert "<key>ProcessType</key>" in content
    assert "<string>Interactive</string>" in content


def test_launchd_plist_no_username_key(backend: LaunchdBackend) -> None:
    """LaunchAgents must not contain UserName -- invalid for agents."""
    content = backend.plist_content()
    assert "<key>UserName</key>" not in content


def test_launchd_plist_pins_to_aqua_session(backend: LaunchdBackend) -> None:
    """LimitLoadToSessionType=Aqua pins voxd to the graphical login session.

    Without the pin, launchd may bootstrap the agent into a Background
    context that lacks CoreAudio session membership -- producing the
    intermittent ``AudioQueueStart failed (-66681)`` after a ~15s block
    that made afplay silently fail dozens of times per session.
    """
    content = backend.plist_content()
    assert "<key>LimitLoadToSessionType</key>" in content
    # Value must appear on the line following the key (adjacent in the
    # emitted plist), not merely somewhere in the document.
    lines = content.splitlines()
    key_index = next(
        i for i, line in enumerate(lines) if "<key>LimitLoadToSessionType</key>" in line
    )
    assert "<string>Aqua</string>" in lines[key_index + 1]


@patch.dict("os.environ", {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"})
def test_launchd_plist_contains_path_from_env() -> None:
    be = LaunchdBackend(lambda: ["/opt/homebrew/bin/voxd", "--port", "8421"])
    content = be.plist_content()
    assert "<key>EnvironmentVariables</key>" in content
    assert "<key>PATH</key>" in content
    assert "/opt/homebrew/bin:/usr/bin:/bin" in content


@patch.dict(
    "os.environ",
    {
        "SSL_CERT_FILE": "/etc/ssl/certs/proxy-ca.pem",
        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/proxy-ca.pem",
    },
    clear=True,
)
def test_launchd_plist_bakes_the_ca_bundle_vars() -> None:
    """The CA vars reach the plist through the backend a user installs with.

    ``VoxdPlist`` is unit-tested directly; this pins the wiring, so a backend
    that stopped consulting the captured environment would fail here even
    while the plist class still passed its own tests.
    """
    be = LaunchdBackend(lambda: ["/usr/local/bin/voxd", "--port", "8421"])
    content = be.plist_content()
    assert "<key>SSL_CERT_FILE</key>" in content
    assert "<key>REQUESTS_CA_BUNDLE</key>" in content
    assert "/etc/ssl/certs/proxy-ca.pem" in content


@patch.dict("os.environ", {}, clear=True)
def test_launchd_plist_omits_the_ca_bundle_vars_when_unset() -> None:
    be = LaunchdBackend(lambda: ["/usr/local/bin/voxd", "--port", "8421"])
    content = be.plist_content()
    assert "<key>SSL_CERT_FILE</key>" not in content
    assert "<key>REQUESTS_CA_BUNDLE</key>" not in content


# ---------------------------------------------------------------------------
# the lifecycle seam -- delegates to the composed LaunchctlAgent
# ---------------------------------------------------------------------------


def test_launchd_is_installed_follows_the_plist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = tmp_path / "com.punt-labs.voxd.plist"
    be, _agent = _backend_with_mock_agent(monkeypatch, plist)
    assert be.is_installed() is False
    plist.write_text("<plist/>")
    assert be.is_installed() is True
    assert be.service_path == str(plist)


def test_launchd_request_stop_delegates_bootout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    be, agent = _backend_with_mock_agent(monkeypatch, tmp_path / "p.plist")
    be.request_stop()
    agent.bootout.assert_called_once_with()


def test_launchd_request_stop_failure_is_backend_command_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed bootout of a loaded job leaves the backend as one typed error."""
    be, agent = _backend_with_mock_agent(monkeypatch, tmp_path / "p.plist")
    agent.bootout.side_effect = LaunchctlError("bootout failed (exit 37)")
    with pytest.raises(BackendCommandError, match="bootout failed"):
        be.request_stop()


def test_launchd_request_start_failure_is_backend_command_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    be, agent = _backend_with_mock_agent(monkeypatch, tmp_path / "p.plist")
    agent.start.side_effect = LaunchctlError("bootstrap failed (exit 5)")
    with pytest.raises(BackendCommandError, match="launchctl bootstrap"):
        be.request_start()
    agent.start.assert_called_once_with()


@pytest.mark.parametrize(
    ("loaded", "pid", "active"),
    [(True, 42, True), (True, None, True), (False, 42, True), (False, None, False)],
)
def test_launchd_is_active_counts_a_lingering_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loaded: bool,
    pid: int | None,
    active: bool,
) -> None:
    """A job still holding a pid has not stopped, whatever launchctl list says."""
    be, agent = _backend_with_mock_agent(monkeypatch, tmp_path / "p.plist")
    agent.is_loaded.return_value = loaded
    agent.pid.return_value = pid
    assert be.is_active() is active


def test_launchd_main_pid_delegates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    be, agent = _backend_with_mock_agent(monkeypatch, tmp_path / "p.plist")
    agent.pid.return_value = 4242
    assert be.main_pid() == 4242


def test_launchd_installed_bind_reads_the_plist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bind comes from the installed plist, not the calling shell."""
    plist = tmp_path / "com.punt-labs.voxd.plist"
    with patch.dict("os.environ", {"VOXD_BIND": "10.0.0.5"}):
        be, _agent = _backend_with_mock_agent(monkeypatch, plist)
        plist.write_text(be.plist_content())
    with patch.dict("os.environ", {"VOXD_BIND": "10.0.0.9"}):
        assert be.installed_bind() == "10.0.0.5"


# ---------------------------------------------------------------------------
# install (fresh, no migration)
# ---------------------------------------------------------------------------


def test_launchd_install_writes_plist_and_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install authors the plist, then delegates bring-up to agent.start()."""
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    plist = agents_dir / "com.punt-labs.voxd.plist"
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    be, agent = _backend_with_mock_agent(monkeypatch, plist)

    be.install("someone")

    assert plist.exists()
    agent.start.assert_called_once_with()


def test_launchd_reinstall_over_stale_agent_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second install over a live agent boots it out, then re-starts it."""
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    plist = agents_dir / "com.punt-labs.voxd.plist"
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    be, agent = _backend_with_mock_agent(monkeypatch, plist)

    be.install("someone")
    assert plist.exists()

    be.request_stop()
    be.install("someone")

    assert agent.start.call_count == 2
    assert agent.bootout.call_count == 1
    assert plist.exists()


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_launchd_uninstall_boots_out_and_removes_plist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uninstall delegates bootout to the agent and unlinks the plist."""
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True)
    plist = agents_dir / "com.punt-labs.voxd.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    be, agent = _backend_with_mock_agent(monkeypatch, plist)

    be.uninstall()

    assert not plist.exists()
    agent.bootout.assert_called_once_with()


def test_launchd_uninstall_noop_when_plist_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uninstall skips bootout when no plist exists."""
    plist = tmp_path / "com.punt-labs.voxd.plist"  # never created
    be, agent = _backend_with_mock_agent(monkeypatch, plist)

    be.uninstall()

    agent.bootout.assert_not_called()


# ---------------------------------------------------------------------------
# round 2: every launchctl failure leaves the backend as BackendCommandError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "agent_call"),
    [("is_active", "is_loaded"), ("main_pid", "pid")],
)
def test_launchd_query_failure_is_backend_command_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    agent_call: str,
) -> None:
    """A3: a failed launchctl query is the lifecycle's typed error, not raw."""
    be, agent = _backend_with_mock_agent(monkeypatch, tmp_path / "p.plist")
    getattr(agent, agent_call).side_effect = LaunchctlError("query failed (exit 1)")
    with pytest.raises(BackendCommandError, match="query failed"):
        getattr(be, method)()


def test_launchd_install_failure_is_backend_command_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B11: install's launchctl failure is typed, so install reports it."""
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    monkeypatch.setattr("punt_vox.service.launchd._LAUNCHD_DIR", agents_dir)
    be, agent = _backend_with_mock_agent(
        monkeypatch, agents_dir / "com.punt-labs.voxd.plist"
    )
    agent.start.side_effect = LaunchctlError("bootstrap failed (exit 5)")
    with pytest.raises(BackendCommandError, match="bootstrap failed"):
        be.install("someone")


def test_launchd_uninstall_failure_is_backend_command_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = tmp_path / "com.punt-labs.voxd.plist"
    plist.write_text("<plist/>")
    be, agent = _backend_with_mock_agent(monkeypatch, plist)
    agent.bootout.side_effect = LaunchctlError("bootout failed (exit 37)")
    with pytest.raises(BackendCommandError, match="bootout failed"):
        be.uninstall()
    assert plist.exists(), "a failed bootout must not remove the plist"
