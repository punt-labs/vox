"""Unit tests for the off-loop fetch measurement and file handoff."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
from threading import Event
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.fetch_digest import FetchDigest

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.fetch_digest import MeasuredFile


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


async def test_executor_rejection_does_not_leak_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "track.mp3"
    path.write_bytes(b"track")
    digest = FetchDigest()

    def reject_submission(*args: object, **kwargs: object) -> None:
        raise RuntimeError("executor is shut down")

    loop = asyncio.get_running_loop()
    with monkeypatch.context() as patch:
        patch.setattr(loop, "run_in_executor", reject_submission)
        for _ in range(3):
            with pytest.raises(RuntimeError, match="executor is shut down"):
                await asyncio.wait_for(digest.measure(path), timeout=5)

    measured = await digest.measure(path)
    with measured.handle:
        assert measured.handle.read() == b"track"


def test_worker_stopped_before_start_does_not_open_file(tmp_path: Path) -> None:
    stopped = Event()
    stopped.set()

    with pytest.raises(asyncio.CancelledError):
        FetchDigest()._read(tmp_path / "missing.mp3", stopped)


async def test_measure_rejects_nonregular_file(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="not a regular file") as exc:
        await FetchDigest().measure(tmp_path)

    assert exc.value.errno == errno.EINVAL


async def test_discard_ignores_cancelled_future(
    caplog: pytest.LogCaptureFixture,
) -> None:
    work: asyncio.Future[MeasuredFile] = asyncio.get_running_loop().create_future()
    work.cancel()

    with caplog.at_level(logging.ERROR):
        FetchDigest._discard(work)

    assert not caplog.records


async def test_discard_logs_abandoned_worker_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    work: asyncio.Future[MeasuredFile] = asyncio.get_running_loop().create_future()
    work.set_exception(OSError("read failed"))

    with caplog.at_level(logging.ERROR):
        FetchDigest._discard(work)

    (record,) = caplog.records
    assert record.message == "Abandoned fetch measurement failed"
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "read failed"
