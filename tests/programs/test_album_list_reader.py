"""Exercise the shared catalog worker's cancellation and cleanup."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from punt_vox.voxd.programs.album_list_reader import AlbumListReader
from punt_vox.voxd.programs.catalog import Album

from .conftest import make_service, seed_album

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.album_id import AlbumId
    from punt_vox.voxd.programs.manifest import AlbumManifest


@pytest.mark.parametrize("worker_fails", [False, True])
async def test_cancelled_list_keeps_slot_until_worker_stops_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    worker_fails: bool,
) -> None:
    root = tmp_path / "programs"
    seed_album(root, 1, album_id="a3f1c9")
    seed_album(root, 1, album_id="7b2e04")
    service = make_service(root)
    reader = AlbumListReader()
    loop = asyncio.get_running_loop()
    read_started = asyncio.Event()
    second_started = asyncio.Event()
    resume_read = threading.Event()
    original_read = Album.read
    submit = Mock(wraps=loop.run_in_executor)
    reads: list[AlbumId] = []

    def read(album: Album) -> AlbumManifest:
        reads.append(album.id)
        loop.call_soon_threadsafe(read_started.set)
        assert resume_read.wait(timeout=5), "listing was not cancelled"
        if worker_fails and len(reads) == 1:
            raise RuntimeError("worker read failed")
        return original_read(album)

    async def second_list() -> list[dict[str, object]]:
        second_started.set()
        return await reader.read(service)

    monkeypatch.setattr(loop, "run_in_executor", submit)
    monkeypatch.setattr(Album, "read", read)
    async with asyncio.TaskGroup() as tasks:
        listing = tasks.create_task(reader.read(service))
        try:
            await asyncio.wait_for(read_started.wait(), timeout=5)
            second = tasks.create_task(second_list())
            await asyncio.wait_for(second_started.wait(), timeout=5)
            submit.assert_called_once()
            listing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await listing
            submit.assert_called_once()
        finally:
            resume_read.set()
        rows = await asyncio.wait_for(second, timeout=5)

    expected = [album.id for album in service.catalog_albums()]
    assert reads == [expected[0], *expected]
    assert [row["id"] for row in rows] == [album_id.value for album_id in expected]
    assert ("Abandoned catalog read failed" in caplog.messages) == worker_fails


async def test_failed_worker_releases_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "programs"
    seed_album(root, 1)
    service = make_service(root)
    reader = AlbumListReader()
    with monkeypatch.context() as patch:
        patch.setattr(
            Album, "read", Mock(side_effect=RuntimeError("worker read failed"))
        )
        with pytest.raises(RuntimeError, match="worker read failed"):
            await reader.read(service)
    rows = await asyncio.wait_for(reader.read(service), timeout=5)
    assert [row["id"] for row in rows] == ["a3f1c9"]
