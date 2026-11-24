"""Tests for the program wire handlers -- thin adapters over the service.

Each handler is driven with a fake WebSocket (records ``send_json``) and a real
filesystem-backed :class:`ProgramService`; posted commands are applied via
``run_once`` so the reply shape *and* the resulting daemon state are asserted.
The handlers never mutate the source -- they POST a signal the sole writer
applies -- so a rejected or unresolvable command surfaces as a boundary error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from typing import TYPE_CHECKING, Self, cast, final
from unittest.mock import Mock

import pytest

from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.catalog import Album
from punt_vox.voxd.programs.command_handler import ProgramCommandHandler
from punt_vox.voxd.programs.list_handler import ListHandler
from punt_vox.voxd.programs.next_handler import NextHandler
from punt_vox.voxd.programs.on_handler import OnHandler
from punt_vox.voxd.programs.select_handler import SelectHandler
from punt_vox.voxd.programs.status_handler import StatusHandler
from punt_vox.voxd.programs.stop_handler import StopHandler

from .conftest import make_service, seed_album

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.manifest import AlbumManifest
    from punt_vox.voxd.programs.service import ProgramService
    from punt_vox.voxd.types import MessageHandler


@final
class FakeWebSocket:
    """Record every ``send_json`` payload the handler emits."""

    __slots__ = ("sent",)
    sent: list[dict[str, object]]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.sent = []
        return self

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)


def _service(tmp_path: Path) -> ProgramService:
    return make_service(tmp_path / "programs")


async def _reply(handler: MessageHandler, msg: dict[str, object]) -> dict[str, object]:
    """Invoke ``handler`` with ``msg`` and return the single reply it sent."""
    ws = FakeWebSocket()
    await handler(msg, cast("WebSocket", ws))
    return ws.sent[0]


class TestStatusHandler:
    async def test_idle_status(self, tmp_path: Path) -> None:
        reply = await _reply(StatusHandler(_service(tmp_path)), {"id": "1"})
        assert reply["type"] == "program_status"
        assert reply["id"] == "1"
        assert isinstance(reply["status"], dict)
        assert reply["status"]["mode"] == "off"

    async def test_status_reflects_a_replayed_selection(self, tmp_path: Path) -> None:
        seed_album(tmp_path / "programs", 1, 2, style="trance", vibe="calm")
        service = _service(tmp_path)
        await _reply(SelectHandler(service), {"id": "p", "style": "trance"})
        await service.run_once()
        reply = await _reply(StatusHandler(service), {"id": "s"})
        status = reply["status"]
        assert isinstance(status, dict)
        assert status["now_playing"] is not None


class TestMutatingHandlers:
    async def test_on_acks_and_activates(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        reply = await _reply(
            OnHandler(service), {"id": "1", "style": "t", "vibe": "calm", "name": "mix"}
        )
        assert reply == {"type": "program_on", "id": "1"}
        await service.run_once()
        service.shutdown()
        assert not service.status().is_idle

    async def test_off_acks(self, tmp_path: Path) -> None:
        reply = await _reply(StopHandler(_service(tmp_path)), {"id": "9"})
        assert reply == {"type": "program_stop", "id": "9"}

    async def test_next_acks(self, tmp_path: Path) -> None:
        reply = await _reply(NextHandler(_service(tmp_path)), {"id": "2"})
        assert reply == {"type": "program_next", "id": "2"}

    async def test_select_by_tags_acks_and_plays(self, tmp_path: Path) -> None:
        seed_album(tmp_path / "programs", 1, 2, 3, style="trance", vibe="calm")
        service = _service(tmp_path)
        reply = await _reply(
            SelectHandler(service), {"id": "3", "style": "trance", "vibe": "calm"}
        )
        assert reply == {"type": "program_select", "id": "3"}
        await service.run_once()
        assert service.status().now_playing is not None

    async def test_select_normalizes_tags_to_match_the_mint_form(
        self, tmp_path: Path
    ) -> None:
        # The on-path stores canonical (trimmed) tags; the select path must apply
        # the same normalization, so a whitespace-padded query still matches.
        seed_album(tmp_path / "programs", 1, 2, style="trance", vibe="calm")
        service = _service(tmp_path)
        reply = await _reply(
            SelectHandler(service), {"id": "3", "style": "  trance  ", "vibe": " calm "}
        )
        # Matched despite the whitespace, rather than a "no albums match" error.
        assert reply == {"type": "program_select", "id": "3"}
        await service.run_once()
        assert service.status().now_playing is not None

    async def test_select_no_match_is_an_error(self, tmp_path: Path) -> None:
        reply = await _reply(
            SelectHandler(_service(tmp_path)), {"id": "3", "style": "ghost"}
        )
        assert reply["type"] == "error"
        assert "no albums match" in str(reply["message"])

    async def test_select_ignores_unknown_field_and_replays_last_played(
        self, tmp_path: Path
    ) -> None:
        # An unknown field ("id_arg") is neither the album_id nor a tag axis, so it
        # is ignored: with no album_id and no tags this is the bare-``play`` path,
        # which repeats the last-played album -- and with none played yet, errors.
        reply = await _reply(
            SelectHandler(_service(tmp_path)), {"id": "3", "id_arg": "x"}
        )
        assert reply["type"] == "error"
        assert "no album played yet" in str(reply["message"])

    @pytest.mark.parametrize("field", ["album_id", "style", "name"])
    async def test_select_non_string_field_is_an_error(
        self, tmp_path: Path, field: str
    ) -> None:
        # A present-but-wrong-typed field is a malformed request, not an absent
        # one: it must reply with an error rather than silently ignoring the field
        # and falling through to a catch-all tag query.
        reply = await _reply(SelectHandler(_service(tmp_path)), {"id": "3", field: 123})
        assert reply["type"] == "error"
        assert field in str(reply["message"])
        assert "must be a string" in str(reply["message"])


class TestListHandler:
    async def test_reads_album_manifests_outside_event_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_album(tmp_path / "programs", 1)
        service = _service(tmp_path)
        loop_thread = threading.get_ident()
        read_threads: list[int] = []
        original_read = Album.read

        def read(album: Album) -> AlbumManifest:
            read_threads.append(threading.get_ident())
            return original_read(album)

        monkeypatch.setattr(Album, "read", read)
        reply = await _reply(ListHandler(service), {"id": "5"})

        assert reply["type"] == "program_list"
        assert len(read_threads) == 1
        assert read_threads[0] != loop_thread, "album manifest read on the daemon loop"

    async def test_lists_albums_with_tags_and_counts(self, tmp_path: Path) -> None:
        root = tmp_path / "programs"
        seed_album(root, 1, 2, style="trance", vibe="calm", album_id="a3f1c9")
        seed_album(root, 1, style="lofi", vibe="focus", album_id="7b2e04")
        reply = await _reply(ListHandler(_service(tmp_path)), {"id": "5"})
        assert reply["type"] == "program_list"
        programs = reply["programs"]
        assert isinstance(programs, list)
        assert {p["id"] for p in programs} == {"a3f1c9", "7b2e04"}
        trance = next(p for p in programs if p["id"] == "a3f1c9")
        assert trance["style"] == "trance"
        assert trance["vibe"] == "calm"
        assert trance["ready"] == 2

    async def test_empty_catalogue(self, tmp_path: Path) -> None:
        reply = await _reply(ListHandler(_service(tmp_path)), {"id": "6"})
        assert reply["programs"] == []

    @pytest.mark.parametrize("worker_fails", [False, True])
    async def test_cancelled_list_keeps_slot_until_worker_stops_reading(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        worker_fails: bool,
    ) -> None:
        root = tmp_path / "programs"
        seed_album(root, 1, album_id="a3f1c9")
        seed_album(root, 1, album_id="7b2e04")
        service = _service(tmp_path)
        handler = ListHandler(service)
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

        async def second_list() -> dict[str, object]:
            second_started.set()
            return await _reply(handler, {"id": "8"})

        monkeypatch.setattr(loop, "run_in_executor", submit)
        monkeypatch.setattr(Album, "read", read)
        async with asyncio.TaskGroup() as tasks:
            listing = tasks.create_task(_reply(handler, {"id": "7"}))
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
            reply = await asyncio.wait_for(second, timeout=5)

        assert reply["type"] == "program_list"
        expected = [album.id for album in service.catalog_albums()]
        assert reads == [expected[0], *expected]
        assert ("Abandoned catalog read failed" in caplog.messages) == worker_fails

    async def test_failed_worker_releases_slot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_album(tmp_path / "programs", 1)
        handler = ListHandler(_service(tmp_path))
        with monkeypatch.context() as patch:
            patch.setattr(
                Album, "read", Mock(side_effect=RuntimeError("worker read failed"))
            )
            with pytest.raises(RuntimeError, match="worker read failed"):
                await _reply(handler, {"id": "7"})
        reply = await asyncio.wait_for(_reply(handler, {"id": "8"}), timeout=5)
        assert len(cast("list[object]", reply["programs"])) == 1

    @pytest.mark.parametrize("read_before_remove", [False, True])
    async def test_omits_album_removed_during_read_without_logging_a_fault(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        read_before_remove: bool,
    ) -> None:
        root = tmp_path / "programs"
        locator = seed_album(root, 1)
        service = _service(tmp_path)
        loop = asyncio.get_running_loop()
        read_started = asyncio.Event()
        resume_read = threading.Event()
        original_read = Album.read

        def read(album: Album) -> AlbumManifest:
            manifest = original_read(album) if read_before_remove else None
            loop.call_soon_threadsafe(read_started.set)
            assert resume_read.wait(timeout=5), "catalog removal did not run"
            return original_read(album) if manifest is None else manifest

        monkeypatch.setattr(Album, "read", read)
        async with asyncio.TaskGroup() as tasks:
            listing = tasks.create_task(_reply(ListHandler(service), {"id": "7"}))
            try:
                await asyncio.wait_for(read_started.wait(), timeout=5)
                shutil.rmtree(root / locator)
                service.catalog.remove(AlbumId("a3f1c9"))
            finally:
                resume_read.set()

        assert listing.result()["programs"] == []
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)

    @pytest.mark.parametrize("missing", [False, True])
    async def test_lists_survive_an_album_gone_unreadable(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, missing: bool
    ) -> None:
        # A list row reads Parts live from disk, so an album deleted after startup
        # raises when its row is built. The query isolates per album -- the broken
        # one is skipped (ERROR-logged) and the healthy album is still returned,
        # rather than the whole list tearing the socket down.
        root = tmp_path / "programs"
        seed_album(root, 1, 2, style="trance", vibe="calm", album_id="a3f1c9")
        gone = seed_album(root, 1, style="lofi", vibe="focus", album_id="7b2e04")
        service = _service(tmp_path)  # scans both into the catalog
        if missing:
            shutil.rmtree(root / gone)
        else:
            manifest_path = root / gone / "manifest.json"
            data = json.loads(manifest_path.read_text())
            data["parts"][0]["index"] = 0
            manifest_path.write_text(json.dumps(data))
        with caplog.at_level(logging.ERROR):
            reply = await _reply(ListHandler(service), {"id": "5"})
        programs = reply["programs"]
        assert isinstance(programs, list)
        assert {p["id"] for p in programs} == {"a3f1c9"}  # the healthy album remains
        error_type = "AlbumNotFoundError" if missing else "ValueError"
        assert any(
            "skipping unreadable album 7b2e04" in (message := record.getMessage())
            and error_type in message
            for record in caplog.records
        )


@final
class _RaisingHandler(ProgramCommandHandler):
    """A handler whose ``_run`` raises a chosen exception -- exercises the boundary."""

    __slots__ = ("_error",)
    _error: Exception
    _WIRE_TYPE = "program_probe"

    def __new__(cls, error: Exception) -> Self:
        self = super().__new__(cls, cast("ProgramService", None))
        self._error = error
        return self

    def _run(self, msg: dict[str, object], /) -> None:
        raise self._error


class TestMutatingBoundary:
    """Every expected domain failure becomes a wire error, not a torn socket.

    ``store.open`` on a deleted album dir raises ``LookupError``; ``store.create``'s
    ``mkdir(exist_ok=False)`` mint-race guard raises ``FileExistsError`` and a full
    or read-only disk raises ``OSError``. Before the fix the boundary caught only
    ``ValueError``, so these escaped, the router tore the socket down, and the
    client saw a generic "connection closed" instead of the cause.
    """

    @pytest.mark.parametrize(
        ("error", "expected_message"),
        [
            # A ValueError is a client rejection: its message echoes the client's
            # own input and crosses verbatim. A LookupError/OSError is a server
            # fault: the wire carries only the generic verdict, never the cause.
            (ValueError("bad request"), "bad request"),
            (LookupError("no saved album at directory 'x'"), "operation failed"),
            (FileExistsError("File exists: 'x'"), "operation failed"),
            (OSError("No space left on device"), "operation failed"),
        ],
    )
    async def test_expected_failures_reply_with_an_error(
        self, error: Exception, expected_message: str
    ) -> None:
        reply = await _reply(_RaisingHandler(error), {"id": "7"})
        assert reply["type"] == "error"
        assert reply["id"] == "7"
        assert reply["message"] == expected_message
