"""Tests for punt_vox.service.port_file_cleanup -- a serve.port clear's result."""

from __future__ import annotations

import pytest

from punt_vox.service.port_file_cleanup import PortFileCleanup


@pytest.mark.parametrize(
    ("result", "settled"),
    [
        (PortFileCleanup.REMOVED, True),
        (PortFileCleanup.ABSENT, True),
        (PortFileCleanup.OTHER_PORT, True),
        (PortFileCleanup.HELD, False),
    ],
)
def test_only_a_decline_leaves_the_cleanup_open(
    result: PortFileCleanup, settled: bool
) -> None:
    assert result.settled is settled
