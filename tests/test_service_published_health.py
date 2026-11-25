"""Tests for punt_vox.service.published_health -- the unattributed door for the token.

Driven through ``DaemonProbe.published_health``, which composes the request
over the process manager's ``serve.port`` and the probe's listener check.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock, probe_over

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.published_port import PublishedPort
from punt_vox.types_health import HealthStatus


def _published(monkeypatch: pytest.MonkeyPatch, reading: PublishedPort) -> MagicMock:
    """Return a process manager whose serve.port reads as *reading*."""
    mgr = MagicMock()
    mgr.port_file = Path("/nonexistent/serve.port")

    def at(path: Path) -> PublishedPort:
        return reading

    monkeypatch.setattr(PublishedPort, "at", staticmethod(at))
    return mgr


def _listening(monkeypatch: pytest.MonkeyPatch, *, accept: bool = True) -> MagicMock:
    connect = MagicMock()
    if not accept:
        connect.side_effect = ConnectionRefusedError
    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    return connect


def test_published_health_asks_without_any_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Our serve.port names 8421, loopback listens: the token goes out, no lsof."""
    _listening(monkeypatch)
    target = MagicMock(host="127.0.0.1", port=8421)
    status = HealthStatus(status="ok", pid=42)
    target.client.return_value.health.return_value = status
    mgr = _published(monkeypatch, PublishedPort(8421))
    assert probe_over(mgr, target).published_health(1.5) is status
    mgr.find_pid_on_port.assert_not_called()
    mgr.open_voxd.assert_not_called()


@pytest.mark.parametrize(
    ("reading", "why"),
    [
        (PublishedPort(None, absent=True), "publishes no port"),
        (PublishedPort(None), "does not name a port"),
        (PublishedPort(9000), "names port 9000, not the service port 8421"),
        (PublishedPort(8421, owner=NotAskedError.not_owned(0)), "belongs to uid 0"),
        (
            PublishedPort(8421, owner=NotAskedError.unreadable("cannot stat x")),
            "cannot stat x",
        ),
    ],
)
def test_published_health_sends_nothing_unless_our_file_names_the_service(
    monkeypatch: pytest.MonkeyPatch, reading: PublishedPort, why: str
) -> None:
    connect = _listening(monkeypatch)
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(_published(monkeypatch, reading), target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    assert isinstance(probe.health_miss(), NotAskedError)
    assert why in str(probe.health_miss())


def test_published_health_unreadable_port_file_is_a_miss_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A serve.port vox cannot read leaves attribution to decide, as the
    listener check does: a voxd lsof would find must not read unknown."""
    connect = _listening(monkeypatch)
    (tmp_path / "serve.port").write_bytes(b"\xff\xfe")
    mgr = MagicMock()
    mgr.port_file = tmp_path / "serve.port"
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert "invalid UTF-8" in str(miss)


def test_published_health_sends_nothing_off_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = _listening(monkeypatch)
    target = MagicMock(host="192.168.1.5", port=8421)
    probe = probe_over(_published(monkeypatch, PublishedPort(8421)), target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    assert "not loopback" in str(probe.health_miss())


def test_published_health_sends_nothing_to_an_empty_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _listening(monkeypatch, accept=False)
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(_published(monkeypatch, PublishedPort(8421)), target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    assert "nothing listens" in str(probe.health_miss())


def test_published_health_connect_failure_is_a_miss_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connect vox cannot complete leaves attribution to decide."""
    connect = MagicMock(side_effect=TimeoutError("timed out"))
    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(_published(monkeypatch, PublishedPort(8421)), target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    assert "cannot connect" in str(probe.health_miss())


def test_published_health_request_gets_what_the_connect_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()

    def connect(address: object, timeout: float) -> object:
        clock.advance(0.25)
        return MagicMock()

    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    target = MagicMock(host="127.0.0.1", port=8421)
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    probe = DaemonProbe(
        _published(monkeypatch, PublishedPort(8421)), lambda: target, Poll(clock)
    )
    probe.published_health(1.5)
    target.client.return_value.health.assert_called_once_with(
        timeout=pytest.approx(1.25)
    )


def test_published_health_connect_that_leaves_less_than_the_floor_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()

    def connect(address: object, timeout: float) -> object:
        clock.advance(1.49)
        return MagicMock()

    monkeypatch.setattr(
        "punt_vox.service.daemon_probe.socket.create_connection", connect
    )
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = DaemonProbe(
        _published(monkeypatch, PublishedPort(8421)), lambda: target, Poll(clock)
    )
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    assert "spent the health budget" in str(probe.health_miss())


def test_published_health_no_reply_is_none_and_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _listening(monkeypatch)
    target = MagicMock(host="127.0.0.1", port=8421)
    rejected = VoxdConnectionError("auth rejected (4401)")
    target.client.return_value.health.side_effect = rejected
    probe = probe_over(_published(monkeypatch, PublishedPort(8421)), target)
    assert probe.published_health(1.5) is None
    assert probe.health_miss() is rejected
