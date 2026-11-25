"""Tests for punt_vox.service.published_health -- the unattributed door for the token.

Driven through ``DaemonProbe.published_health``, which composes the request
over the process manager's ``serve.port`` and the probe's listener check.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock, probe_over

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.health import HealthTarget
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.published_port import PublishedPort
from punt_vox.types_health import HealthStatus


def _published(monkeypatch: pytest.MonkeyPatch, reading: PublishedPort) -> MagicMock:
    """Return a process manager whose run files read as *reading*.

    Its bounded ``ps`` identity says the publisher runs voxd.
    """
    mgr = MagicMock()
    mgr.port_file = Path("/nonexistent/serve.port")
    mgr.within.return_value.identity.return_value.is_voxd.return_value = True
    mgr.within.return_value.holds_port.return_value = True

    def at(path: Path) -> PublishedPort:
        return reading

    monkeypatch.setattr(PublishedPort, "at", staticmethod(at))
    return mgr


def _ours() -> PublishedPort:
    """The open gate: our serve.port names 8421, our serve.pid this process."""
    return PublishedPort(8421, os.getpid())


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
    mgr = _published(monkeypatch, _ours())
    assert probe_over(mgr, target).published_health(1.5) is status
    mgr.find_pid_on_port.assert_not_called()
    mgr.open_voxd.assert_not_called()


@pytest.mark.parametrize(
    ("reading", "why"),
    [
        (PublishedPort(None, refusal=NotAskedError.unpublished()), "publishes no port"),
        (
            PublishedPort(None, refusal=NotAskedError.unparseable()),
            "does not name a port",
        ),
        (
            PublishedPort(9000, os.getpid()),
            "names port 9000, not the service port 8421",
        ),
        (
            PublishedPort(8421, refusal=NotAskedError.not_owned("serve.port", 0)),
            "serve.port belongs to uid 0",
        ),
        (PublishedPort(8421, refusal=NotAskedError.no_pid()), "publishes no pid"),
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


def test_a_stale_port_file_with_a_foreign_listener_sends_no_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The crash leftover: our serve.port names 8421, serve.pid a pid that is
    gone, and another user's process accepts connections on the port."""
    connect = _listening(monkeypatch)
    with subprocess.Popen(["true"]) as done:
        done.wait()
    dead = done.pid
    (tmp_path / "serve.port").write_text("8421")
    (tmp_path / "serve.pid").write_text(str(dead))
    mgr = MagicMock()
    mgr.port_file = tmp_path / "serve.port"
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    mgr.within.assert_not_called()
    assert str(probe.health_miss()) == (
        f"voxd pid {dead} that published serve.port is gone; no token sent unattributed"
    )


def test_a_live_publisher_of_this_user_is_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Real run files naming this process, a voxd per ``ps``: the token goes out."""
    _listening(monkeypatch)
    (tmp_path / "serve.port").write_text("8421")
    (tmp_path / "serve.pid").write_text(str(os.getpid()))
    mgr = MagicMock()
    mgr.port_file = tmp_path / "serve.port"
    mgr.within.return_value.identity.return_value.is_voxd.return_value = True
    mgr.within.return_value.holds_port.return_value = True
    target = MagicMock(host="127.0.0.1", port=8421)
    status = HealthStatus(status="ok", pid=os.getpid())
    target.client.return_value.health.return_value = status
    assert probe_over(mgr, target).published_health(1.5) is status
    mgr.within.return_value.identity.assert_called_once_with(os.getpid())


def test_a_publisher_that_is_not_voxd_sends_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = _listening(monkeypatch)
    mgr = _published(monkeypatch, _ours())
    seen = mgr.within.return_value.identity.return_value
    seen.is_voxd.return_value = False
    seen.command = "/usr/bin/nc -l 8421"
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    assert str(probe.health_miss()) == (
        f"pid {os.getpid()} named in serve.pid is not voxd "
        "(command: /usr/bin/nc -l 8421); no token sent unattributed"
    )


def test_a_publisher_gone_before_ps_sends_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _listening(monkeypatch)
    mgr = _published(monkeypatch, _ours())
    mgr.within.return_value.identity.return_value = None
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    assert "is gone" in str(probe.health_miss())


def test_a_ps_that_cannot_run_is_a_miss_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _listening(monkeypatch)
    mgr = _published(monkeypatch, _ours())
    mgr.within.return_value.identity.side_effect = PortProbeError(
        "cannot run ps: timed out after 0.4 seconds"
    )
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    assert str(probe.health_miss()) == (
        "cannot run ps: timed out after 0.4 seconds; no token sent unattributed"
    )


def test_the_ps_is_cut_to_the_request_bound_and_the_holder_check_to_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The holder check gets its own slice; it can never spend the request floor."""
    _listening(monkeypatch)
    mgr = _published(monkeypatch, _ours())
    target = MagicMock(host="127.0.0.1", port=8421)
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    probe_over(mgr, target).published_health(1.5)
    ps_bound, holder_bound = (call.args[0] for call in mgr.within.call_args_list)
    assert ps_bound.remaining() == pytest.approx(1.5)
    assert holder_bound.remaining() == pytest.approx(DaemonProbe.HOLDER_CHECK_S)
    mgr.within.return_value.holds_port.assert_called_once_with(
        os.getpid(), "127.0.0.1", 8421
    )


def test_a_holder_check_that_takes_its_whole_bound_leaves_the_request_its_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow lsof -p spends only its own slice: the request still goes out
    within the share, with at least the floor."""
    _listening(monkeypatch)
    clock = FakeClock()
    bounds: list[float] = []

    def within(bound: object) -> MagicMock:
        remaining = bound.remaining()  # type: ignore[attr-defined]
        bounded = MagicMock()
        bounded.identity.return_value.is_voxd.return_value = True

        def slow_holder_check(pid: int, host: str, port: int) -> bool:
            bounds.append(remaining)
            clock.advance(remaining)  # the check takes everything it was given
            return True

        bounded.holds_port.side_effect = slow_holder_check
        return bounded

    mgr = _published(monkeypatch, _ours())
    mgr.within.side_effect = within
    target = MagicMock(host="127.0.0.1", port=8421)
    target.client.return_value.health.return_value = HealthStatus(status="ok")
    probe = DaemonProbe(mgr, lambda: target, Poll(clock))
    assert probe.published_health(1.5) is not None
    assert bounds == [pytest.approx(DaemonProbe.HOLDER_CHECK_S)]
    (request_timeout,) = (
        c.kwargs["timeout"] for c in target.client.return_value.health.mock_calls
    )
    assert request_timeout == pytest.approx(1.5 - DaemonProbe.HOLDER_CHECK_S)
    assert request_timeout >= HealthRequest.MIN_S
    assert clock.monotonic() <= 1.5


def test_a_holder_check_with_no_slice_left_before_the_floor_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With only the floor left after the ps, there is no slice for the check."""
    connect = _listening(monkeypatch)
    clock = FakeClock()
    mgr = _published(monkeypatch, _ours())

    def slow_ps(pid: int) -> MagicMock:
        clock.advance(1.0)  # 0.5 s left of 1.5: exactly the request floor
        seen = MagicMock()
        seen.is_voxd.return_value = True
        return seen

    mgr.within.return_value.identity.side_effect = slow_ps
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = DaemonProbe(mgr, lambda: target, Poll(clock))
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    mgr.within.return_value.holds_port.assert_not_called()
    assert "cannot tell whether the publisher holds port 8421" in str(
        probe.health_miss()
    )


def test_the_holder_check_is_made_for_the_targets_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """voxd on 127.0.0.1 while the unit polls ::1: the check names ::1, and a
    listener on another address closes the gate."""
    connect = _listening(monkeypatch)
    mgr = _published(monkeypatch, _ours())
    mgr.within.return_value.holds_port.return_value = False
    target = MagicMock(host="::1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    mgr.within.return_value.holds_port.assert_called_once_with(os.getpid(), "::1", 8421)


def test_a_live_own_voxd_that_does_not_hold_the_port_sends_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shutdown window: the socket is closed, the process lingers."""
    connect = _listening(monkeypatch)
    mgr = _published(monkeypatch, _ours())
    mgr.within.return_value.holds_port.return_value = False
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    assert str(probe.health_miss()) == (
        f"voxd pid {os.getpid()} that published serve.port no longer holds port "
        "8421; no token sent unattributed"
    )


def test_a_holder_check_that_cannot_be_made_sends_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = _listening(monkeypatch)
    mgr = _published(monkeypatch, _ours())
    mgr.within.return_value.holds_port.side_effect = PortProbeError(
        "cannot run lsof: timed out after 1.2 seconds"
    )
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(mgr, target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    assert str(probe.health_miss()) == (
        "cannot tell whether the publisher holds port 8421: cannot run lsof: "
        "timed out after 1.2 seconds; no token sent unattributed"
    )


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
    probe = probe_over(_published(monkeypatch, _ours()), target)
    assert probe.published_health(1.5) is None
    target.client.assert_not_called()
    connect.assert_not_called()
    assert "not loopback" in str(probe.health_miss())


def test_published_health_sends_nothing_to_an_empty_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _listening(monkeypatch, accept=False)
    target = MagicMock(host="127.0.0.1", port=8421)
    probe = probe_over(_published(monkeypatch, _ours()), target)
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
    probe = probe_over(_published(monkeypatch, _ours()), target)
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
    probe = DaemonProbe(_published(monkeypatch, _ours()), lambda: target, Poll(clock))
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
    probe = DaemonProbe(_published(monkeypatch, _ours()), lambda: target, Poll(clock))
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
    probe = probe_over(_published(monkeypatch, _ours()), target)
    assert probe.published_health(1.5) is None
    assert probe.health_miss() is rejected


def _slow_target(
    clock: FakeClock, lookup_s: float
) -> tuple[MagicMock, Callable[[], HealthTarget]]:
    """Return a target whose lookup (the installed bind) takes *lookup_s*."""
    target = MagicMock(host="127.0.0.1", port=8421)
    target.client.return_value.health.return_value = HealthStatus(status="ok")

    def resolve() -> HealthTarget:
        clock.advance(lookup_s)
        return cast("HealthTarget", target)

    return target, resolve


def test_the_target_lookup_spends_from_the_health_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow read of the installed bind leaves the request only the rest."""
    _listening(monkeypatch)
    clock = FakeClock()
    target, resolve = _slow_target(clock, 0.25)
    probe = DaemonProbe(_published(monkeypatch, _ours()), resolve, Poll(clock))
    probe.published_health(1.5)
    target.client.return_value.health.assert_called_once_with(
        timeout=pytest.approx(1.25)
    )


def test_a_target_lookup_that_spends_the_share_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole call finishes within the share: no request, the step named."""
    connect = _listening(monkeypatch)
    clock = FakeClock()
    target, resolve = _slow_target(clock, 1.4)
    probe = DaemonProbe(_published(monkeypatch, _ours()), resolve, Poll(clock))
    assert probe.published_health(1.5) is None
    assert clock.monotonic() <= 1.5
    target.client.assert_not_called()
    connect.assert_not_called()
    miss = probe.health_miss()
    assert isinstance(miss, NotAskedError)
    assert "installed bind" in str(miss)
