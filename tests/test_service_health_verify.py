"""Unit tests for the post-install health verifier.

``HealthVerifier.verify`` polls voxd's gated health endpoint through
``DaemonProbe.wait_up`` -- the one poll install, start and restart share. The
contract: return the health snapshot once voxd answers, and raise
``ServiceHealthError`` (never a bare ``RuntimeError``) when the daemon
registers but never serves -- the silent-down failure mode.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from punt_vox.service.daemon_probe import DaemonProbe
from punt_vox.service.health import HealthTarget
from punt_vox.service.health_verify import HealthVerifier, ServiceHealthError
from punt_vox.types_health import HealthStatus

_SERVICE_PATH = "/Library/LaunchAgents/com.punt-labs.voxd.plist"


def _probe(answer: HealthStatus | None) -> MagicMock:
    probe = MagicMock()
    probe.wait_up.return_value = answer
    probe.target = HealthTarget("")
    return probe


def test_verify_returns_what_the_daemon_reported() -> None:
    status = HealthStatus(status="ok", pid=42, daemon_version="5.0.6")
    probe = _probe(status)

    assert HealthVerifier(probe, _SERVICE_PATH).verify() is status

    probe.wait_up.assert_called_once_with(5.0)


def test_verify_raises_service_health_error_on_deadline() -> None:
    """A daemon that never serves raises ``ServiceHealthError``, not RuntimeError.

    The typed error is what lets ``vox install`` degrade gracefully while
    ``vox daemon install`` fails loudly -- both catch by type, not message.
    """
    with pytest.raises(ServiceHealthError, match="never became reachable") as info:
        HealthVerifier(_probe(None), _SERVICE_PATH).verify()

    # The failure message points the operator at the exact service unit.
    assert _SERVICE_PATH in str(info.value)
    assert "127.0.0.1:8421" in str(info.value)


def test_for_backend_targets_the_installed_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verifier polls the bind the backend wrote, not the shell's."""
    monkeypatch.setenv("VOXD_BIND", "10.0.0.9")

    def never(self: DaemonProbe, deadline_s: float) -> None:
        return None

    monkeypatch.setattr(DaemonProbe, "wait_up", never)
    backend = MagicMock()
    backend.installed_bind.return_value = "10.0.0.5"
    backend.service_path = _SERVICE_PATH
    verifier = HealthVerifier.for_backend(backend)
    with pytest.raises(ServiceHealthError, match=r"10\.0\.0\.5:8421"):
        verifier.verify()


def test_service_health_error_is_runtime_error() -> None:
    """``ServiceHealthError`` stays a ``RuntimeError`` subtype for the tuple catch."""
    assert issubclass(ServiceHealthError, RuntimeError)
