"""Tests for punt_vox.voxd.play_handler -- daemon-host playback of store files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from punt_vox.voxd.play_handler import PlayHandler
from punt_vox.voxd.playback import PlaybackItem
from punt_vox.voxd.record_store import RecordStore


def _playback_that_completes() -> MagicMock:
    """A PlaybackQueue mock whose enqueue immediately signals completion."""

    async def _enqueue(item: PlaybackItem) -> None:
        item.notify.set()

    playback = MagicMock()
    playback.enqueue = AsyncMock(side_effect=_enqueue)
    return playback


def _capturing_ws() -> tuple[MagicMock, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    async def _send(payload: dict[str, object]) -> None:
        sent.append(payload)

    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=_send)
    return ws, sent


class TestPlayHandler:
    """play resolves a store ref and plays it on the daemon, not the client."""

    def test_play_routes_through_daemon(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        rec = store.root / "a1b2c3.mp3"
        rec.write_bytes(b"\xff\xfb\x90\x00" * 4)
        playback = _playback_that_completes()
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "play", "id": "p1", "ref": "a1b2c3.mp3"}
        asyncio.run(PlayHandler(playback=playback, store=store)(msg, ws))

        playback.enqueue.assert_awaited_once()
        item = playback.enqueue.await_args.args[0]
        assert isinstance(item, PlaybackItem)
        assert item.path == rec.resolve()  # the in-store path, played daemon-side
        assert [p["type"] for p in sent] == ["playing", "done"]

    def test_play_ref_outside_root_rejected(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        playback = _playback_that_completes()
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "play", "id": "p1", "ref": "/etc/passwd"}
        asyncio.run(PlayHandler(playback=playback, store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "absolute" in str(sent[-1]["message"])
        playback.enqueue.assert_not_awaited()

    def test_play_traversal_ref_rejected(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        playback = _playback_that_completes()
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "play", "id": "p1", "ref": "../../etc/x"}
        asyncio.run(PlayHandler(playback=playback, store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        playback.enqueue.assert_not_awaited()

    def test_missing_ref_is_an_error(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        playback = _playback_that_completes()
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "play", "id": "p1"}
        asyncio.run(PlayHandler(playback=playback, store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "requires a ref" in str(sent[-1]["message"])

    def test_unknown_recording_is_an_error(self, tmp_path: Path) -> None:
        """A well-formed ref that does not exist in the store is refused."""
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        playback = _playback_that_completes()
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "play", "id": "p1", "ref": "nope.mp3"}
        asyncio.run(PlayHandler(playback=playback, store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "no recording" in str(sent[-1]["message"])
        playback.enqueue.assert_not_awaited()
