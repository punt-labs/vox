"""Tests for punt_vox.service.voxd_bind.VoxdBind.

Install and health share this one check, so a bind install accepts is a bind
health can poll, and a bind health would refuse never reaches a unit file.
"""

from __future__ import annotations

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.voxd_bind import VoxdBind


@pytest.mark.parametrize("raw", ["", "  "])
def test_no_bind_polls_loopback(raw: str) -> None:
    assert VoxdBind(raw).poll_host() == "127.0.0.1"


def test_a_concrete_ip_is_polled_exactly_without_its_padding() -> None:
    assert VoxdBind(" 10.0.0.5 ").poll_host() == "10.0.0.5"


@pytest.mark.parametrize("raw", ["localhost", "voxd.internal", "10.0.0"])
def test_a_name_or_malformed_address_is_refused_with_the_remedy(raw: str) -> None:
    with pytest.raises(PortProbeError) as caught:
        VoxdBind(raw)
    message = str(caught.value)
    assert f"VOXD_BIND {raw!r} is not an IP address" in message
    assert "e.g. 127.0.0.1" in message
    assert "vox daemon install" in message


def test_from_environ_reads_voxd_bind_and_defaults_to_none_set() -> None:
    assert VoxdBind.from_environ({}).poll_host() == "127.0.0.1"
    with pytest.raises(PortProbeError):
        VoxdBind.from_environ({"VOXD_BIND": "localhost"})
