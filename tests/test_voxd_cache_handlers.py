"""Tests for punt_vox.voxd.cache_handlers -- cache_status and cache_clear.

Each handler holds its cache operation as an injected collaborator, so a test
drives it with a lambda that returns a canned ``CacheInfo`` / count or raises an
``OSError`` -- no daemon, no real cache directory. A fake WebSocket captures the
reply frames. Cache routing is covered at the CLI layer
(``tests/test_cli.py``); here we prove the wire contract: the reply mirrors the
cache functions' return shapes, and a filesystem fault is an id-stamped
operational fault (ERROR "operation failed"), never a silent success or a raise
that tears the connection down.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, cast, final

import pytest

from punt_vox import cache, dirs, paths
from punt_vox.cache import CacheInfo, CacheKey
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.cache_handlers import CacheHandler
from punt_vox.voxd.cache_reply_types import CacheClearedReply, CacheStatusReply
from punt_vox.voxd.synthesis_cache import SynthesisCache
from punt_vox.voxd.synthesis_result import SynthesisOutcome

if TYPE_CHECKING:
    from starlette.websockets import WebSocket


def _capturing_ws() -> tuple[WebSocket, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    @final
    class _WS:
        async def send_json(self, payload: dict[str, object]) -> None:
            sent.append(payload)

    return cast("WebSocket", _WS()), sent


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["status", "clear"])
async def test_slow_scan_does_not_block_the_loop(verb: str) -> None:
    """The loop must release the scan while the offloaded handler is waiting."""
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = Event()

    def scan[T](result: T) -> T:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(1), "cache scan blocked the event loop"
        return result

    handler: Callable[[dict[str, object], WebSocket], Coroutine[None, None, None]]
    if verb == "status":
        handler = CacheHandler.status(lambda: scan(CacheInfo(0, 0, Path("cache"))))
    else:
        handler = CacheHandler.clear(lambda: scan(0))
    ws, sent = _capturing_ws()
    work = asyncio.create_task(handler({"id": "r1"}, ws))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not work.done()
    finally:
        release.set()
        await work
    assert sent[-1]["id"] == "r1"


@pytest.mark.parametrize(
    ("handler", "contract"),
    [
        (
            CacheHandler.status(lambda: CacheInfo(2, 128, Path("cache"))),
            CacheStatusReply(
                type="cache_status", entries=2, size_bytes=128, path="cache"
            ),
        ),
        (
            CacheHandler.clear(lambda: 2),
            CacheClearedReply(type="cache_cleared", cleared=2),
        ),
    ],
)
async def test_wire_reply_fields_match_contract(
    handler: CacheHandler, contract: CacheStatusReply | CacheClearedReply
) -> None:
    ws, sent = _capturing_ws()
    await handler({"id": "contract"}, ws)
    assert sent == [{**contract, "id": "contract"}]


@pytest.mark.asyncio
async def test_clear_waits_for_synthesis_cache_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clear must not remove a live cache staging file or miss its published MP3."""
    directory = tmp_path / "cache"
    monkeypatch.setattr(cache, "CACHE_DIR", directory)
    source = tmp_path / "output.mp3"
    source.write_bytes(b"audio")
    loop = asyncio.get_running_loop()
    writing, clearing = asyncio.Event(), asyncio.Event()
    release = Event()
    copy2 = shutil.copy2

    def pause_write(src: Path, dst: Path) -> None:
        loop.call_soon_threadsafe(writing.set)
        assert release.wait(5), "test did not release the cache writer"
        copy2(src, dst)

    def clear() -> int:
        loop.call_soon_threadsafe(clearing.set)
        return cache.cache_clear()

    monkeypatch.setattr(shutil, "copy2", pause_write)
    lookup = SynthesisCache(CacheKey("text", None, None), SynthesisSpec(), 4)
    ws, sent = _capturing_ws()
    store = asyncio.create_task(lookup.store(SynthesisOutcome(source, False)))
    try:
        await asyncio.wait_for(writing.wait(), timeout=5)
        request = asyncio.create_task(CacheHandler.clear(clear)({"id": "c3"}, ws))
        await asyncio.wait_for(clearing.wait(), timeout=5)
        done, _ = await asyncio.wait({request}, timeout=0.05)
        assert not done
    finally:
        release.set()
        published = await store
    await request
    assert published is not None
    assert not list(directory.iterdir())
    assert sent == [{"type": "cache_cleared", "id": "c3", "cleared": 1}]


class TestCacheStatus:
    def test_reply_relativizes_the_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The in-jail cache dir crosses as its relative `cache`, never a host prefix.

        The daemon cache lives at ``<state>/cache``; the wire carries only the
        relative form so a client learns the logical location, never the absolute
        prefix (home + username) -- the a7dd chroot invariant.
        """
        state = (Path(str(tmp_path)) / "state").resolve()
        (state / "cache").mkdir(parents=True)
        monkeypatch.setattr(paths, "user_state_dir", lambda: state)
        monkeypatch.setattr(dirs, "default_output_dir", lambda: state / "out")
        info = CacheInfo(entries=3, size_bytes=2048, path=state / "cache")
        ws, sent = _capturing_ws()
        asyncio.run(CacheHandler.status(lambda: info)({"id": "s1"}, ws))
        assert sent[-1] == {
            "type": "cache_status",
            "id": "s1",
            "entries": 3,
            "size_bytes": 2048,
            "path": "cache",
        }
        assert not str(sent[-1]["path"]).startswith("/")  # no absolute prefix

    def test_out_of_jail_path_never_leaks_a_prefix(self) -> None:
        """A path under neither root falls back to the bare name, never a prefix."""
        info = CacheInfo(entries=0, size_bytes=0, path=Path("/daemon/home/user/cache"))
        ws, sent = _capturing_ws()
        asyncio.run(CacheHandler.status(lambda: info)({"id": "s2"}, ws))
        assert sent[-1]["path"] == "cache"  # bare name, no home/username prefix
        assert sent[-1]["entries"] == 0
        assert sent[-1]["size_bytes"] == 0

    def test_stat_io_error_is_an_operational_fault(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An OSError sizing the cache is a server-side fault, not a rejection.

        ``cache_status`` sizes each entry with a ``stat``; a permission change
        mid-scan raises ``OSError``. The handler routes it through
        ``WireReply.fault`` -- an id-stamped error frame audited at ERROR
        "operation failed", never WARNING "rejected op" -- and reaches the
        assertions without a raise (the connection stays intact).
        """

        def boom() -> CacheInfo:
            raise OSError("permission denied on cache dir")

        ws, sent = _capturing_ws()
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            asyncio.run(CacheHandler.status(boom)({"id": "s9"}, ws))
        assert sent[-1]["type"] == "error"
        assert sent[-1]["id"] == "s9"
        # An OSError with no in-jail filename carries no relative form -- the wire
        # gets the generic verdict; the raw detail stays in the host-local log.
        assert sent[-1]["message"] == "operation failed"
        assert any(
            r.levelno == logging.ERROR
            and "operation failed" in r.getMessage()
            and "permission denied on cache dir" in r.getMessage()
            for r in caplog.records
        )
        assert not any("rejected op" in r.getMessage() for r in caplog.records)


class TestCacheClear:
    def test_reply_mirrors_delete_count(self) -> None:
        ws, sent = _capturing_ws()
        asyncio.run(CacheHandler.clear(lambda: 7)({"id": "c1"}, ws))
        assert sent[-1] == {"type": "cache_cleared", "id": "c1", "cleared": 7}

    def test_nothing_to_clear_reports_zero(self) -> None:
        ws, sent = _capturing_ws()
        asyncio.run(CacheHandler.clear(lambda: 0)({"id": "c2"}, ws))
        assert sent[-1] == {"type": "cache_cleared", "id": "c2", "cleared": 0}

    def test_unlink_io_error_is_an_operational_fault(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A denied unlink (an OSError) faults rather than reporting a false success.

        A partial clear that hit a permission error must not answer "cleared N";
        the handler routes the ``OSError`` through ``WireReply.fault`` (ERROR
        "operation failed") and replies an id-stamped error frame.
        """

        def boom() -> int:
            raise PermissionError("[Errno 13] Permission denied")

        ws, sent = _capturing_ws()
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            asyncio.run(CacheHandler.clear(boom)({"id": "c9"}, ws))
        assert sent[-1]["type"] == "error"
        assert sent[-1]["id"] == "c9"
        # No in-jail filename on the OSError -- the wire is the generic verdict,
        # the raw "Permission denied" detail is kept in the host-local log.
        assert sent[-1]["message"] == "operation failed"
        assert any(
            r.levelno == logging.ERROR
            and "operation failed" in r.getMessage()
            and "Permission denied" in r.getMessage()
            for r in caplog.records
        )
        assert not any("rejected op" in r.getMessage() for r in caplog.records)
