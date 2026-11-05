"""Tests for punt_vox.service.health -- where the token-bearing request goes.

Every connection value comes from a trusted source: the host from the bind the
backend wrote into the installed unit, the port from ``DEFAULT_PORT``, the
token from ``serve.token``. Nothing is read from the calling shell.
"""

from __future__ import annotations

import ipaddress
from unittest.mock import MagicMock

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.health import DaemonNotUpError, HealthTarget
from punt_vox.service.process import DEFAULT_PORT

# Construct the unspecified addresses rather than spelling "0.0.0.0" as a
# literal, which trips ruff S104 (bind-all-interfaces) even in a test value.
_IPV4_WILDCARD = str(ipaddress.IPv4Address(0))
_IPV6_WILDCARD = str(ipaddress.IPv6Address(0))


def test_health_target_pins_installed_default_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll port is DEFAULT_PORT regardless of a stray VOXD_PORT env."""
    monkeypatch.setenv("VOXD_PORT", "9999")
    assert HealthTarget("").port == DEFAULT_PORT


@pytest.mark.parametrize("bind", ["", "   "])
def test_health_target_empty_bind_maps_to_loopback(bind: str) -> None:
    """A unit with no concrete bind leaves voxd on loopback."""
    assert HealthTarget(bind).host == "127.0.0.1"


@pytest.mark.parametrize(
    "wildcard",
    [_IPV4_WILDCARD, _IPV6_WILDCARD, f"  {_IPV4_WILDCARD}  "],
)
def test_health_target_wildcard_bind_maps_to_loopback(wildcard: str) -> None:
    """Wildcard (unspecified) binds resolve loopback -- voxd accepts it there."""
    assert HealthTarget(wildcard).host == "127.0.0.1"


@pytest.mark.parametrize("bind", ["192.168.1.50", "  192.168.1.50  "])
def test_health_target_concrete_bind_used_directly(bind: str) -> None:
    """A concrete bind is polled directly, stripped of surrounding space."""
    assert HealthTarget(bind).host == "192.168.1.50"


@pytest.mark.parametrize("bind", ["voxd.internal", "localhost"])
def test_health_target_refuses_a_hostname_bind(bind: str) -> None:
    """A name could resolve to an address voxd never bound (localhost to ::1
    while voxd holds 127.0.0.1), where another local user would get the token;
    and its lookup is not bounded by any deadline."""
    with pytest.raises(PortProbeError, match=rf"VOXD_BIND {bind!r} is not an IP"):
        HealthTarget(bind)


@pytest.mark.parametrize(
    ("bind", "host"), [("::1", "::1"), ("fe80::1%lo", "fe80::1%lo")]
)
def test_health_target_keeps_an_ipv6_bind(bind: str, host: str) -> None:
    assert HealthTarget(bind).host == host


def test_health_target_bind_comes_from_backend_not_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shell's VOXD_BIND never redirects the request: only the unit's does."""
    monkeypatch.setenv("VOXD_BIND", "10.0.0.9")
    assert HealthTarget("").host == "127.0.0.1"
    assert HealthTarget("10.0.0.5").host == "10.0.0.5"


# ---------------------------------------------------------------------------
# client() -- token only from the run file
# ---------------------------------------------------------------------------


def _capture_clients(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        return MagicMock()

    monkeypatch.setattr("punt_vox.service.health.VoxClientSync", _factory)
    return captured


def test_health_target_client_reads_run_file_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """client() passes the run-file token, never a stray shell VOXD_TOKEN."""
    monkeypatch.setenv("VOXD_TOKEN", "shell-token")
    monkeypatch.setattr(
        "punt_vox.service.health.RunFiles.token", lambda: "authoritative-run-token"
    )
    captured = _capture_clients(monkeypatch)

    HealthTarget("").client()

    assert captured == [
        {"host": "127.0.0.1", "port": DEFAULT_PORT, "token": "authoritative-run-token"}
    ]


def test_no_token_file_raises_daemon_not_up_and_builds_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no serve.token, no client exists, so VOXD_TOKEN cannot leak."""
    monkeypatch.setenv("VOXD_TOKEN", "shell-token")
    monkeypatch.setattr("punt_vox.service.health.RunFiles.token", lambda: None)
    captured = _capture_clients(monkeypatch)

    with pytest.raises(DaemonNotUpError, match=r"serve\.token"):
        HealthTarget("").client()

    assert captured == []
