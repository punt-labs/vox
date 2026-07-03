"""Tests for punt_vox.service.types — the shared platform domain alias."""

from __future__ import annotations

from typing import get_args

from punt_vox.service.types import PlatformName


def test_platform_name_domain_is_macos_and_linux() -> None:
    """PlatformName single-sources exactly the two supported operating systems."""
    assert set(get_args(PlatformName.__value__)) == {"macos", "linux"}
