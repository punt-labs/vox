"""Unit tests for the off-loop fetch measurement and file handoff."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.fetch_digest import FetchDigest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("bound", [0, -1])
def test_invalid_read_bound(bound: int) -> None:
    with pytest.raises(ValueError, match="chunk_bytes must be positive"):
        FetchDigest(bound)


def test_measure_returns_rewound_file_and_exact_totals(tmp_path: Path) -> None:
    path = tmp_path / "track.mp3"
    contents = b"abcdefghij"
    path.write_bytes(contents)

    measured = asyncio.run(FetchDigest(4).measure(path))

    with measured.handle:
        assert measured.total == len(contents)
        assert measured.chunks == 3
        assert measured.sha256 == hashlib.sha256(contents).hexdigest()
        assert measured.handle.read() == contents
