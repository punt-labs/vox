"""Unit tests for the post-install health verifier.

``HealthVerifier.verify`` waits for the service manager to report the unit
active, then polls voxd's gated health endpoint through
``DaemonProbe.wait_up`` -- the one poll install, start and restart share. The
contract: return the health snapshot once voxd answers, and raise
``ServiceHealthError`` (never a bare ``RuntimeError``), carrying the last
cause, when the unit never runs or the daemon never serves.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import FakeClock

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.service.clock import Poll
from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.health import HealthTarget
from punt_vox.service.health_verify import HealthVerifier, ServiceHealthError
from punt_vox.service.launchd import LaunchdBackend
from punt_vox.service.query_answer import QueryAnswer
from punt_vox.service.systemd import PrivilegedRun, SystemdBackend
from punt_vox.types_health import HealthStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.service.backend import InstallableBackend

_SERVICE_PATH = "/Library/LaunchAgents/com.punt-labs.voxd.plist"


def _backend(*, active: bool = True, bind: str = "") -> MagicMock:
    backend = MagicMock()
    backend.is_active_within.return_value = QueryAnswer.of(holds=active)
    backend.installed_bind.return_value = bind
    backend.service_path = _SERVICE_PATH
    return backend


def _probe(answer: HealthStatus | None, miss: Exception | None = None) -> MagicMock:
    probe = MagicMock()
    probe.wait_up.return_value = answer
    probe.health_miss.return_value = miss
    probe.target = HealthTarget("")
    probe.poll = Poll(FakeClock())
    return probe


def test_verify_returns_what_the_daemon_reported() -> None:
    status = HealthStatus(status="ok", pid=42, daemon_version="5.0.6")
    probe = _probe(status)

    assert HealthVerifier(_backend(), probe).verify() is status

    probe.wait_up.assert_called_once_with(5.0)


def test_verify_raises_service_health_error_on_deadline() -> None:
    """A daemon that never serves raises ``ServiceHealthError``, not RuntimeError.

    The typed error is what lets ``vox install`` degrade gracefully while
    ``vox daemon install`` fails loudly -- both catch by type, not message.
    """
    with pytest.raises(ServiceHealthError, match="never became reachable") as info:
        HealthVerifier(_backend(), _probe(None)).verify()

    # The failure message points the operator at the exact service unit.
    assert _SERVICE_PATH in str(info.value)
    assert "127.0.0.1:8421" in str(info.value)


def test_verify_carries_the_last_health_cause() -> None:
    """B9: the cause is in the message and chained, not discarded."""
    rejected = VoxdConnectionError("auth rejected (4401)")
    with pytest.raises(ServiceHealthError, match=r"auth rejected \(4401\)") as info:
        HealthVerifier(_backend(), _probe(None, rejected)).verify()
    assert info.value.__cause__ is rejected


def test_verify_fails_when_the_unit_never_becomes_active() -> None:
    """B12: an installed unit that is not running is a failure, not success."""
    probe = _probe(HealthStatus(status="ok"))
    with pytest.raises(ServiceHealthError, match="not active"):
        HealthVerifier(_backend(active=False), probe).verify()
    probe.wait_up.assert_not_called()


def test_for_backend_targets_the_installed_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verifier polls the bind the backend wrote, not the shell's."""
    monkeypatch.setenv("VOXD_BIND", "10.0.0.9")

    def never(self: DaemonProbe, deadline_s: float) -> None:
        return None

    monkeypatch.setattr(DaemonProbe, "wait_up", never)
    verifier = HealthVerifier.for_backend(_backend(bind="10.0.0.5"))
    with pytest.raises(ServiceHealthError, match=r"10\.0\.0\.5:8421"):
        verifier.verify()


def test_service_health_error_is_runtime_error() -> None:
    """``ServiceHealthError`` stays a ``RuntimeError`` subtype for the tuple catch."""
    assert issubclass(ServiceHealthError, RuntimeError)


def test_verify_never_lets_an_active_query_outlive_the_wait() -> None:
    """Each is-active query is bounded by what is left of the 5 s wait."""
    clock = FakeClock()
    probe = _probe(HealthStatus(status="ok"))
    probe.poll = Poll(clock)
    seen: list[tuple[float, float]] = []

    def not_yet(within_s: float) -> QueryAnswer:
        seen.append((clock.monotonic(), within_s))
        return QueryAnswer.of(holds=False)

    backend = _backend()
    backend.is_active_within.side_effect = not_yet
    with pytest.raises(ServiceHealthError, match="not active"):
        HealthVerifier(backend, probe).verify()
    assert seen
    assert all(budget > 0 and at + budget <= 5.0 for at, budget in seen)
    assert clock.monotonic() <= 5.0


def _systemd_hung(monkeypatch: pytest.MonkeyPatch) -> InstallableBackend:
    monkeypatch.setattr(
        "punt_vox.service.systemd.subprocess.run",
        MagicMock(side_effect=subprocess.TimeoutExpired("systemctl", 0.1)),
    )
    return SystemdBackend(
        lambda: ["/usr/local/bin/voxd"], PrivilegedRun(MagicMock(), lambda: False)
    )


def _launchd_hung(monkeypatch: pytest.MonkeyPatch) -> InstallableBackend:
    monkeypatch.setattr(
        "punt_vox.service.launchctl_query.subprocess.run",
        MagicMock(side_effect=subprocess.TimeoutExpired("launchctl", 0.1)),
    )
    return LaunchdBackend(lambda: ["/usr/local/bin/voxd"])


@pytest.mark.parametrize(
    ("hung", "query"),
    [
        (_systemd_hung, "systemctl is-active voxd"),
        (_launchd_hung, "launchctl list com.punt-labs.voxd"),
    ],
)
def test_verify_names_a_manager_query_that_never_answered(
    monkeypatch: pytest.MonkeyPatch,
    hung: Callable[[pytest.MonkeyPatch], InstallableBackend],
    query: str,
) -> None:
    """S2: a hung systemctl/launchctl is named, not blamed on voxd's logs."""
    backend = hung(monkeypatch)
    probe = _probe(HealthStatus(status="ok"))
    with pytest.raises(ServiceHealthError, match=f"{query} did not answer") as info:
        HealthVerifier(backend, probe).verify()
    assert "daemon logs" not in str(info.value)
    probe.wait_up.assert_not_called()
