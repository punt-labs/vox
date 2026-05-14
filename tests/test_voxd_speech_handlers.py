"""Tests for punt_vox.voxd.speech_handlers -- synthesize and record handlers."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from punt_vox.voxd.dedup import OnceDedup
from punt_vox.voxd.playback import PlaybackItem, PlaybackQueue
from punt_vox.voxd.speech_handlers import SynthesizeHandler
from punt_vox.voxd.synthesis import SynthesisPipeline


def _make_synthesize_handler(
    *,
    synthesis: SynthesisPipeline | None = None,
    playback: PlaybackQueue | None = None,
    once_dedup: OnceDedup | None = None,
) -> SynthesizeHandler:
    """Build a SynthesizeHandler for testing."""
    pb = playback or PlaybackQueue()
    syn = synthesis or SynthesisPipeline(playback_mutex=pb.mutex)
    od = once_dedup or OnceDedup()
    return SynthesizeHandler(synthesis=syn, playback=pb, once_dedup=od)


class TestHandleSynthesizeShortCircuit:
    """SynthesizeHandler skips try_direct_play for cloud providers."""

    def test_cloud_provider_skips_direct_play(self) -> None:
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.try_direct_play = AsyncMock(return_value=None)
        mock_synth.synthesize_to_file = AsyncMock(side_effect=RuntimeError("stop here"))
        handler = _make_synthesize_handler(synthesis=mock_synth)
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "1",
            "text": "hello",
            "provider": "elevenlabs",
        }

        asyncio.run(handler(msg, websocket))

        mock_synth.try_direct_play.assert_not_called()

    def test_local_provider_calls_direct_play(self) -> None:
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.try_direct_play = AsyncMock(return_value=0)
        handler = _make_synthesize_handler(synthesis=mock_synth)
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "2",
            "text": "hello",
            "provider": "espeak",
        }

        asyncio.run(handler(msg, websocket))

        mock_synth.try_direct_play.assert_called_once()
        call_args = mock_synth.try_direct_play.call_args
        # spec is the second positional argument
        spec = call_args[0][1]
        assert spec.provider == "espeak"


class TestHandleSynthesizeOnceFlag:
    """Integration tests for SynthesizeHandler with the once flag."""

    @staticmethod
    def _make_stubbed_handler(
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[SynthesizeHandler, list[str]]:
        """Build a handler with fake synthesis and instant playback."""
        synthesis_calls: list[str] = []

        async def fake_synthesize(*args: object, **_kwargs: object) -> Path:
            synthesis_calls.append(str(args[0]))
            return Path("/tmp/fake.mp3")

        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = fake_synthesize

        monkeypatch.setattr(
            "punt_vox.voxd.speech_handlers._LOCAL_PROVIDERS", set[str]()
        )
        monkeypatch.setattr(
            "punt_vox.voxd.speech_handlers.auto_detect_provider", lambda: "elevenlabs"
        )

        handler = _make_synthesize_handler(synthesis=mock_synth)

        class _InstantPlaybackQueue:
            async def put(self, item: PlaybackItem) -> None:
                item.notify.set()

        handler._playback._queue = _InstantPlaybackQueue()  # type: ignore[assignment]
        return handler, synthesis_calls

    @pytest.mark.asyncio
    async def test_once_null_does_not_dedupe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without once, identical requests both proceed (regression)."""
        handler, synthesis_calls = self._make_stubbed_handler(monkeypatch)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "hello",
        }
        await handler(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
        }
        await handler(msg2, ws)

        assert len(synthesis_calls) == 2

    @pytest.mark.asyncio
    async def test_once_set_dedups_identical_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With once=600, the second identical request returns deduped."""
        handler, synthesis_calls = self._make_stubbed_handler(monkeypatch)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "wall msg",
            "once": 600,
        }
        await handler(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "wall msg",
            "once": 600,
        }
        await handler(msg2, ws)

        assert len(synthesis_calls) == 1

        all_calls = ws.send_json.call_args_list
        sent_msgs = [c[0][0] for c in all_calls]
        deduped_msgs = [m for m in sent_msgs if m.get("deduped") is True]
        assert len(deduped_msgs) == 1
        deduped = deduped_msgs[0]
        assert deduped["id"] == "b"
        assert deduped["type"] == "done"
        assert "original_played_at" in deduped
        assert "ttl_seconds_remaining" in deduped
        assert deduped["ttl_seconds_remaining"] > 0

    @pytest.mark.asyncio
    async def test_once_zero_does_not_dedupe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """once=0 is treated as null per the spec -- must not dedupe."""
        handler, synthesis_calls = self._make_stubbed_handler(monkeypatch)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "hello",
            "once": 0,
        }
        await handler(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
            "once": 0,
        }
        await handler(msg2, ws)

        assert len(synthesis_calls) == 2
