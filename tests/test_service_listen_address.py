"""Tests for punt_vox.service.listen_address -- which listeners serve the host."""

from __future__ import annotations

import pytest

from punt_vox.service.listen_address import ListenAddress

V4 = "0100007F"  # 127.0.0.1 as /proc/net/tcp prints it
V4_ANY = "00000000"
V6_LOOP = "00000000000000000000000001000000"  # ::1 as /proc/net/tcp6 prints it
V6_ANY = "00000000000000000000000000000000"
V4_MAPPED = "0000000000000000FFFF00000100007F"  # ::ffff:127.0.0.1


def test_port_is_kept() -> None:
    assert ListenAddress("127.0.0.1", 8421).port == 8421


@pytest.mark.parametrize(
    ("family", "name", "host", "serves"),
    [
        pytest.param("IPv4", "127.0.0.1:8421", "127.0.0.1", True, id="exact-v4"),
        pytest.param("IPv4", "127.0.0.1:8421", "::1", False, id="v4-not-v6-host"),
        pytest.param("IPv6", "[::1]:8421", "::1", True, id="exact-v6"),
        pytest.param("IPv6", "[::1]:8421", "127.0.0.1", False, id="v6-not-v4-host"),
        pytest.param("IPv4", "*:8421", "127.0.0.1", True, id="v4-wildcard-v4"),
        pytest.param("IPv6", "*:8421", "::1", True, id="v6-wildcard-v6"),
        pytest.param("IPv6", "*:8421", "127.0.0.1", False, id="v6-wildcard-not-v4"),
        pytest.param("IPv4", "*:8421", "::1", False, id="v4-wildcard-not-v6"),
        pytest.param(
            "IPv6", "[::ffff:127.0.0.1]:8421", "127.0.0.1", True, id="v4-mapped"
        ),
        pytest.param("IPv4", "127.0.0.2:8421", "127.0.0.1", False, id="other-loopback"),
        pytest.param("IPv4", "127.0.0.1:9000", "127.0.0.1", False, id="other-port"),
    ],
)
def test_lsof_matches_only_a_listener_that_serves_the_host(
    family: str, name: str, host: str, serves: bool
) -> None:
    assert ListenAddress(host, 8421).lsof_matches(family, name) is serves


def test_proc_forms_for_an_ipv4_host() -> None:
    """tcp: exact and the IPv4 wildcard; tcp6: only the v4-mapped form, never [::]."""
    address = ListenAddress("127.0.0.1", 8421)
    assert address.proc_forms("tcp") == {f"{V4}:20E5", f"{V4_ANY}:20E5"}
    assert address.proc_forms("tcp6") == {f"{V4_MAPPED}:20E5"}


def test_proc_forms_for_an_ipv6_host() -> None:
    """tcp6: exact and the IPv6 wildcard; no tcp row ever serves an IPv6 host."""
    address = ListenAddress("::1", 8421)
    assert address.proc_forms("tcp6") == {f"{V6_LOOP}:20E5", f"{V6_ANY}:20E5"}
    assert address.proc_forms("tcp") == frozenset()


def test_proc_forms_carry_the_port_in_hex() -> None:
    assert ListenAddress("127.0.0.1", 80).proc_forms("tcp") == {
        f"{V4}:0050",
        f"{V4_ANY}:0050",
    }


def test_a_hostname_is_refused() -> None:
    """Only an IP literal: a name could resolve anywhere, and the forms are bytes."""
    with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6"):
        ListenAddress("localhost", 8421)


def test_a_scoped_ipv6_host_matches_without_its_zone() -> None:
    """A Linux bind may carry a zone (``::1%1``); the kernel's tables and
    lsof's names carry the address bytes alone, so the zone is dropped here."""
    address = ListenAddress("::1%1", 8421)
    assert address.proc_forms("tcp6") == {f"{V6_LOOP}:20E5", f"{V6_ANY}:20E5"}
    assert address.lsof_matches("IPv6", "[::1]:8421") is True
    assert address.lsof_matches("IPv6", "*:8421") is True


def test_a_scoped_link_local_host_is_refused_naming_the_zone() -> None:
    """The kernel row carries address bytes only, so with the zone dropped a
    target on one interface would match a listener on another: fe80::1%2 and
    fe80::1%3 are different sockets. Only loopback, one interface by
    definition, may carry a zone."""
    with pytest.raises(ValueError, match=r"fe80::1%2 .*zone") as refused:
        ListenAddress("fe80::1%2", 8421)
    assert "zone 2" in str(refused.value)
