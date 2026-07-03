"""Tests for punt_vox.service.health — host/port/token for the install poll."""

from __future__ import annotations

import ipaddress
from unittest.mock import MagicMock

import pytest

from punt_vox.service.health import HealthTarget
from punt_vox.service.process import DEFAULT_PORT
from punt_vox.service.systemd import SystemdBackend

# ---------------------------------------------------------------------------
# port derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_pins_installed_default_port(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll port is DEFAULT_PORT regardless of a stray VOXD_PORT env."""
    monkeypatch.setenv("VOXD_PORT", "9999")
    assert HealthTarget(platform).port == DEFAULT_PORT


# ---------------------------------------------------------------------------
# host derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_unset_bind_maps_to_loopback(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset VOXD_BIND resolves the loopback health host on both backends."""
    monkeypatch.delenv("VOXD_BIND", raising=False)
    assert HealthTarget(platform).host == "127.0.0.1"


# Construct the unspecified addresses rather than spelling "0.0.0.0" as a
# literal, which trips ruff S104 (bind-all-interfaces) even in a test value.
_IPV4_WILDCARD = str(ipaddress.IPv4Address(0))
_IPV6_WILDCARD = str(ipaddress.IPv6Address(0))


@pytest.mark.parametrize("platform", ["macos", "linux"])
@pytest.mark.parametrize(
    "wildcard",
    [_IPV4_WILDCARD, _IPV6_WILDCARD, f"  {_IPV4_WILDCARD}  "],
)
def test_health_target_wildcard_bind_maps_to_loopback(
    wildcard: str,
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wildcard (unspecified) binds resolve loopback — voxd accepts it there."""
    monkeypatch.setenv("VOXD_BIND", wildcard)
    assert HealthTarget(platform).host == "127.0.0.1"


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_concrete_bind_used_directly(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concrete, safe bind is polled directly on both backends."""
    monkeypatch.setenv("VOXD_BIND", "192.168.1.50")
    assert HealthTarget(platform).host == "192.168.1.50"


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_health_target_hostname_bind_used_directly(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-IP bind value (hostname) is polled as given, not loopback."""
    monkeypatch.setenv("VOXD_BIND", "voxd.internal")
    assert HealthTarget(platform).host == "voxd.internal"


def test_health_target_systemd_rejected_bind_maps_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VOXD_BIND rejected by safe_systemd_value resolves to loopback (linux).

    ``SystemdBackend.unit_content`` embeds ``VOXD_BIND`` only when
    ``safe_systemd_value`` accepts it. A trailing newline is rejected, so the
    unit drops the variable and voxd binds its DEFAULT_HOST loopback. The
    health host must match that -- 127.0.0.1 -- not the rejected raw address,
    or the poll false-fails a healthy daemon.
    """
    monkeypatch.setenv("VOXD_BIND", "192.168.1.50\n")
    assert not SystemdBackend.safe_systemd_value("192.168.1.50\n")
    assert HealthTarget("linux").host == "127.0.0.1"


def test_health_target_launchd_does_not_apply_systemd_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launchd embeds VOXD_BIND verbatim, so the systemd gate must not apply.

    The same value the systemd gate rejects is passed through by launchd,
    which has no safety gate. The health host must therefore resolve the
    address (whitespace-normalized), not loopback -- the resolution diverges
    by backend exactly as the units do.
    """
    monkeypatch.setenv("VOXD_BIND", "192.168.1.50\n")
    assert HealthTarget("macos").host == "192.168.1.50"


# ---------------------------------------------------------------------------
# client() — token resolution from the run file
# ---------------------------------------------------------------------------


def test_health_target_client_reads_run_file_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """client() passes the run-file token, never a stray shell VOXD_TOKEN."""
    monkeypatch.delenv("VOXD_BIND", raising=False)
    monkeypatch.setattr(
        "punt_vox.service.health.read_token_file",
        lambda: "authoritative-run-token",
    )

    captured: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        return MagicMock()

    monkeypatch.setattr("punt_vox.service.health.VoxClientSync", _factory)

    HealthTarget("macos").client()

    assert captured, "VoxClientSync was never constructed"
    assert captured[0]["host"] == "127.0.0.1"
    assert captured[0]["port"] == DEFAULT_PORT
    assert captured[0]["token"] == "authoritative-run-token"


def test_health_target_client_token_none_when_run_file_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no serve.token yet, client() passes token=None, not a shell env."""
    monkeypatch.delenv("VOXD_BIND", raising=False)
    monkeypatch.setattr(
        "punt_vox.service.health.read_token_file",
        lambda: None,
    )

    captured: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        return MagicMock()

    monkeypatch.setattr("punt_vox.service.health.VoxClientSync", _factory)

    HealthTarget("macos").client()

    assert captured, "VoxClientSync was never constructed"
    assert captured[0]["token"] is None
