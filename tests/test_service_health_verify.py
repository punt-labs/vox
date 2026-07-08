"""Unit tests for the post-install health verifier.

``HealthVerifier.verify`` polls voxd's health endpoint after the service
manager accepts the job. The contract: return quietly once voxd answers,
retry transient startup errors until the deadline, and raise
``ServiceHealthError`` (never a bare ``RuntimeError``) when the daemon
registers but never serves -- the silent-down failure mode.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.health_verify import (
    HealthVerifier,
    ServiceHealthError,
)

_SERVICE_PATH = Path("/Library/LaunchAgents/com.punt-labs.voxd.plist")


def _stub_health(
    monkeypatch: pytest.MonkeyPatch,
    *,
    side_effect: object = None,
    return_value: object = None,
) -> MagicMock:
    """Route ``HealthTarget.client().health()`` through a controllable mock.

    Also drops the retry sleep and stubs the token read so the poll neither
    blocks on real time nor touches the run-file filesystem.
    """
    client = MagicMock()
    if side_effect is not None:
        client.health.side_effect = side_effect
    else:
        client.health.return_value = return_value

    def _factory(**_kwargs: object) -> MagicMock:
        return client

    monkeypatch.setattr("punt_vox.service.health.VoxClientSync", _factory)
    monkeypatch.setattr("punt_vox.service.health.read_token_file", lambda: None)
    monkeypatch.setattr("punt_vox.service.health_verify.time.sleep", MagicMock())
    return client


def test_verify_returns_when_daemon_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon that answers health on the first probe verifies with no raise."""
    client = _stub_health(monkeypatch, return_value={"status": "ok"})

    HealthVerifier("macos", _SERVICE_PATH).verify()

    client.health.assert_called_once()


def test_verify_retries_transient_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receive timeout during startup is retried, not fatal.

    ``VoxdProtocolError`` surfaces while voxd is still binding its port. The
    poll must sleep and retry until the daemon answers rather than failing on
    the first hiccup.
    """
    client = _stub_health(
        monkeypatch,
        side_effect=[
            VoxdProtocolError("timeout waiting for response to 'health'"),
            {"status": "ok"},
        ],
    )

    HealthVerifier("macos", _SERVICE_PATH).verify()

    assert client.health.call_count == 2


def test_verify_raises_service_health_error_on_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon that never serves raises ``ServiceHealthError``, not RuntimeError.

    The typed error is what lets ``vox install`` degrade gracefully while
    ``vox daemon install`` fails loudly -- both catch by type, not message.
    """
    monkeypatch.setattr("punt_vox.service.health_verify._HEALTH_DEADLINE_S", 0.05)
    connection_error = VoxdConnectionError("connection refused")
    client = _stub_health(monkeypatch, side_effect=connection_error)

    with pytest.raises(ServiceHealthError, match="never became reachable") as exc_info:
        HealthVerifier("macos", _SERVICE_PATH).verify()

    # The failure message points the operator at the exact service unit, and the
    # last transport error is chained so the root cause is not swallowed.
    assert str(_SERVICE_PATH) in str(exc_info.value)
    assert exc_info.value.__cause__ is connection_error
    assert client.health.called


def test_service_health_error_is_runtime_error() -> None:
    """``ServiceHealthError`` stays a ``RuntimeError`` subtype for the tuple catch."""
    assert issubclass(ServiceHealthError, RuntimeError)
