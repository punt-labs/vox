"""Tests for punt_vox.providers.stream_file -- all of the stream, or nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from punt_vox.providers.stream_file import StreamFile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _failing_after(*chunks: bytes) -> Iterator[bytes]:
    yield from chunks
    msg = "connection dropped"
    raise ConnectionError(msg)


def test_writes_every_chunk(tmp_path: Path) -> None:
    target = tmp_path / "out.mp3"

    written = StreamFile(target).write(iter([b"ab", b"cde"]))

    assert written == 5
    assert target.read_bytes() == b"abcde"


def test_creates_the_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out.mp3"

    StreamFile(target).write([b"x"])

    assert target.exists()


def test_leaves_only_the_target_on_success(tmp_path: Path) -> None:
    target = tmp_path / "out.mp3"

    StreamFile(target).write([b"x"])

    assert list(tmp_path.iterdir()) == [target]


def test_replaces_an_existing_target(tmp_path: Path) -> None:
    """The synthesis pipeline pre-creates its output path; it must be replaced."""
    target = tmp_path / "out.mp3"
    target.write_bytes(b"")

    StreamFile(target).write([b"new"])

    assert target.read_bytes() == b"new"


def test_mid_stream_failure_propagates_and_leaves_nothing(tmp_path: Path) -> None:
    target = tmp_path / "out.mp3"

    with pytest.raises(ConnectionError, match="dropped"):
        StreamFile(target).write(_failing_after(b"partial"))

    assert list(tmp_path.iterdir()) == []


def test_failure_on_first_chunk_leaves_nothing(tmp_path: Path) -> None:
    target = tmp_path / "out.mp3"

    with pytest.raises(ConnectionError):
        StreamFile(target).write(_failing_after())

    assert list(tmp_path.iterdir()) == []


def test_empty_stream_is_an_error_and_leaves_nothing(tmp_path: Path) -> None:
    target = tmp_path / "out.mp3"

    with pytest.raises(RuntimeError, match="no audio data"):
        StreamFile(target).write([])

    assert list(tmp_path.iterdir()) == []
