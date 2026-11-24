"""Tests for punt_vox.voxd.speech_handlers -- synthesize and record handlers."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from punt_vox import cache
from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import (
    ProviderConfigError,
    UnsendableHeaderError,
)
from punt_vox.types_errors import VoiceNotFoundError
from punt_vox.types_provider_errors import (
    ProviderAuthError,
    ProviderUnavailableError,
    UnknownProviderError,
)
from punt_vox.voxd.dedup import OnceDedup
from punt_vox.voxd.playback import PlaybackItem, PlaybackQueue
from punt_vox.voxd.speech_handlers import SynthesizeHandler
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.synthesis_result import SynthesisOutcome


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

    @pytest.mark.skipif(
        shutil.which("espeak-ng") is None and shutil.which("espeak") is None,
        reason="requires a real espeak voice registry",
    )
    def test_real_local_rejection_logs_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        handler = _make_synthesize_handler()
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "real-local-rejection",
            "provider": "espeak",
            "voice": "smoke-voice-marker-xyz",
            "text": "Private speech must not reach the log.",
        }

        with caplog.at_level(logging.DEBUG, logger="punt_vox"):
            asyncio.run(handler(msg, websocket))

        failures = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(failures) == 1
        record = failures[0]
        assert record.name == "punt_vox.voxd.wire_reply"
        assert record.levelno == logging.WARNING
        assert record.exc_info is None
        assert "real-local-rejection" in record.getMessage()
        assert "smoke-voice-marker-xyz" not in caplog.text
        assert "available:" not in caplog.text
        assert all(r.exc_info is None and not r.exc_text for r in caplog.records)
        assert "voice_not_found" in record.getMessage()
        assert "Private speech must not reach the log." not in caplog.text
        websocket.send_json.assert_awaited_once()
        reply = websocket.send_json.await_args.args[0]
        assert reply["id"] == "real-local-rejection"
        assert reply["type"] == "error"
        assert "smoke-voice-marker-xyz" in reply["message"]

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

    @pytest.mark.parametrize(
        ("rejection", "reason"),
        [
            (
                ProviderUnavailableError("espeak", "provider unavailable"),
                "provider_unavailable",
            ),
            (ProviderAuthError("espeak", 401), "provider_auth"),
            (UnknownProviderError("espeak", ["say"]), "unknown_provider"),
            (VoiceNotFoundError("missing", ["en"]), "voice_not_found"),
            (CaBundleError("unusable CA bundle"), "ca_bundle"),
            (ProviderConfigError("invalid provider configuration"), "provider_config"),
            (UnsendableHeaderError("header cannot be sent"), "provider_config"),
        ],
    )
    def test_direct_play_caller_errors_log_once(
        self,
        rejection: ValueError,
        reason: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(
            "punt_vox.voxd.synthesis.get_provider", MagicMock(side_effect=rejection)
        )
        handler = _make_synthesize_handler()
        websocket = MagicMock()
        websocket.send_json = AsyncMock()

        with caplog.at_level(logging.DEBUG, logger="punt_vox"):
            asyncio.run(
                handler(
                    {
                        "id": "typed-local",
                        "provider": "espeak",
                        "text": "Private speech",
                    },
                    websocket,
                )
            )

        [record] = [
            r
            for r in caplog.records
            if r.name == "punt_vox.voxd.wire_reply" and r.levelno >= logging.WARNING
        ]
        assert record.name == "punt_vox.voxd.wire_reply"
        assert record.levelno == logging.WARNING
        assert record.exc_info is None
        assert "typed-local" in record.getMessage()
        assert reason in record.getMessage()
        assert str(rejection) not in caplog.text
        assert "Private speech" not in caplog.text
        websocket.send_json.assert_awaited_once_with(
            {"type": "error", "id": "typed-local", "message": str(rejection)}
        )

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


class TestSynthesizeParseGuard:
    """A malformed wire field is a clean error frame, not a torn connection."""

    @pytest.mark.asyncio
    async def test_non_string_voice_is_a_clean_error(self) -> None:
        """A non-string typed field yields an id-stamped error frame; the parse
        must not escape the handler and tear the connection down."""
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = AsyncMock()
        handler = _make_synthesize_handler(synthesis=mock_synth)
        sent: list[dict[str, object]] = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=sent.append)

        msg: dict[str, object] = {"id": "1", "text": "hello", "voice": 123}
        await handler(msg, ws)

        assert sent[-1]["type"] == "error"
        assert sent[-1]["id"] == "1"
        assert "must be a string" in str(sent[-1]["message"])
        mock_synth.synthesize_to_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_string_text_is_a_clean_error(self) -> None:
        """A non-string required text yields an id-stamped error frame; 123 must
        not coerce to "123", synthesis must not run, and the connection stays up."""
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = AsyncMock()
        handler = _make_synthesize_handler(synthesis=mock_synth)
        sent: list[dict[str, object]] = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=sent.append)

        msg: dict[str, object] = {"id": "1", "text": 123}
        await handler(msg, ws)

        assert sent[-1]["type"] == "error"
        assert sent[-1]["id"] == "1"
        assert "text must be a string" in str(sent[-1]["message"])
        mock_synth.synthesize_to_file.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "text", ["", "  ", "\t\n"], ids=["empty", "spaces", "tab-nl"]
    )
    async def test_blank_text_is_rejected_before_synthesis(self, text: str) -> None:
        """Whitespace-only text is empty text: rejected at the boundary with the
        same clear error as "", never sent to the provider."""
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = AsyncMock(
            side_effect=RuntimeError("provider rejected blank text")
        )
        handler = _make_synthesize_handler(synthesis=mock_synth)
        sent: list[dict[str, object]] = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=sent.append)

        msg: dict[str, object] = {"id": "1", "text": text, "provider": "elevenlabs"}
        await handler(msg, ws)

        assert sent == [{"type": "error", "id": "1", "message": "empty text"}]
        mock_synth.synthesize_to_file.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "api_key",
        [
            "SECRETKEY123\n",
            "SECRET\u00a0KEY123",
            "SECRETKEY123\u200b",
            "SECRET\u2013KEY123",
            "SECRET\u2019KEY123",
            "SECRETKEY123 ",
        ],
        ids=["newline", "nbsp", "zwsp", "en-dash", "smart-quote", "trailing-space"],
    )
    async def test_unsendable_api_key_is_rejected_without_its_value(
        self, api_key: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A per-request key that cannot go in a header never reaches a provider,
        and neither the reply nor the log quotes it."""
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = AsyncMock()
        handler = _make_synthesize_handler(synthesis=mock_synth)
        sent: list[dict[str, object]] = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=sent.append)
        msg: dict[str, object] = {
            "id": "1",
            "text": "hello",
            "provider": "elevenlabs",
            "api_key": api_key,
        }

        with caplog.at_level(logging.DEBUG):
            await handler(msg, ws)

        assert sent[-1]["type"] == "error"
        assert "api_key contains a character no HTTP header may carry" in str(
            sent[-1]["message"]
        )
        assert "SECRET" not in str(sent)
        assert "SECRET" not in caplog.text
        mock_synth.synthesize_to_file.assert_not_called()


class TestSynthesisFaultClassification:
    """Synthesis and direct-play failures audit as faults, not client rejections.

    A failed synthesis or a nonzero/exception direct-play exit is a server-side
    operational failure -- it reaches the client on the error frame but routes
    through WireReply.fault (ERROR "operation failed"), never WARNING "rejected
    op", since the client's request was well-formed and the daemon-side work is
    what broke. Parse rejections (TestSynthesizeParseGuard) stay errors.
    """

    @staticmethod
    def _capturing_ws() -> tuple[MagicMock, list[dict[str, object]]]:
        sent: list[dict[str, object]] = []
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=sent.append)
        return ws, sent

    @staticmethod
    def _assert_faulted(
        caplog: pytest.LogCaptureFixture,
        sent: list[dict[str, object]],
        detail: str,
    ) -> None:
        # The wire carries only the generic verdict -- a non-OSError fault (or an
        # rc) has no relative form -- while the raw *detail* stays in the log.
        assert sent[-1]["type"] == "error"
        assert sent[-1]["message"] == "operation failed"
        assert any(
            r.levelno == logging.ERROR
            and "operation failed" in r.getMessage()
            and detail in r.getMessage()
            for r in caplog.records
        )
        assert not any("rejected op" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_synthesis_failure_is_a_fault(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.try_direct_play = AsyncMock(return_value=None)
        mock_synth.synthesize_to_file = AsyncMock(
            side_effect=RuntimeError("provider 500")
        )
        handler = _make_synthesize_handler(synthesis=mock_synth)
        ws, sent = self._capturing_ws()
        msg: dict[str, object] = {"id": "s1", "text": "hi", "provider": "elevenlabs"}
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            await handler(msg, ws)
        self._assert_faulted(caplog, sent, "provider 500")

    @pytest.mark.asyncio
    async def test_local_play_exception_is_a_fault(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.try_direct_play = AsyncMock(
            return_value=RuntimeError("espeak crash")
        )
        handler = _make_synthesize_handler(synthesis=mock_synth)
        ws, sent = self._capturing_ws()
        msg: dict[str, object] = {"id": "s2", "text": "hi", "provider": "espeak"}
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            await handler(msg, ws)
        self._assert_faulted(caplog, sent, "espeak crash")

    @pytest.mark.asyncio
    async def test_local_play_nonzero_rc_is_a_fault(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.try_direct_play = AsyncMock(return_value=3)
        handler = _make_synthesize_handler(synthesis=mock_synth)
        ws, sent = self._capturing_ws()
        msg: dict[str, object] = {"id": "s3", "text": "hi", "provider": "espeak"}
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            await handler(msg, ws)
        self._assert_faulted(caplog, sent, "rc=3")


class TestHandleSynthesizeOnceFlag:
    """Integration tests for SynthesizeHandler with the once flag."""

    @staticmethod
    def _make_stubbed_handler(
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[SynthesizeHandler, list[str]]:
        """Build a handler with fake synthesis and instant playback."""
        synthesis_calls: list[str] = []

        async def fake_synthesize(*args: object, **_kwargs: object) -> SynthesisOutcome:
            synthesis_calls.append(str(args[0]))
            return SynthesisOutcome(path=Path("/tmp/fake.mp3"), cached=False)

        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = fake_synthesize

        monkeypatch.setattr(
            "punt_vox.voxd.speech_handlers._LOCAL_PROVIDERS", set[str]()
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
            "provider": "elevenlabs",
        }
        await handler(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
            "provider": "elevenlabs",
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
            "provider": "elevenlabs",
            "once": 600,
        }
        await handler(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "wall msg",
            "provider": "elevenlabs",
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
            "provider": "elevenlabs",
            "once": 0,
        }
        await handler(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
            "provider": "elevenlabs",
            "once": 0,
        }
        await handler(msg2, ws)

        assert len(synthesis_calls) == 2


class TestHandleSynthesizeCachedSignal:
    """SynthesizeHandler rides the cache hit/miss flag on the 'playing' response."""

    @staticmethod
    def _drive_with_cached(
        monkeypatch: pytest.MonkeyPatch,
        *,
        cached: bool,
    ) -> list[dict[str, object]]:
        """Run the handler once with a stubbed outcome; return sent messages."""

        async def fake_synthesize(*_a: object, **_k: object) -> SynthesisOutcome:
            return SynthesisOutcome(path=Path("/tmp/fake.mp3"), cached=cached)

        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = fake_synthesize
        monkeypatch.setattr(
            "punt_vox.voxd.speech_handlers._LOCAL_PROVIDERS", set[str]()
        )
        handler = _make_synthesize_handler(synthesis=mock_synth)

        class _InstantPlaybackQueue:
            async def put(self, item: PlaybackItem) -> None:
                item.notify.set()

        handler._playback._queue = _InstantPlaybackQueue()  # type: ignore[assignment]

        ws = MagicMock()
        ws.send_json = AsyncMock()
        asyncio.run(
            handler(
                {
                    "type": "synthesize",
                    "id": "x",
                    "text": "hi",
                    "provider": "elevenlabs",
                },
                ws,
            )
        )
        return [call[0][0] for call in ws.send_json.call_args_list]

    def test_playing_reports_cache_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = self._drive_with_cached(monkeypatch, cached=True)
        playing = [m for m in sent if m.get("type") == "playing"]
        assert len(playing) == 1
        assert playing[0]["cached"] is True

    def test_playing_reports_cache_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = self._drive_with_cached(monkeypatch, cached=False)
        playing = [m for m in sent if m.get("type") == "playing"]
        assert len(playing) == 1
        assert playing[0]["cached"] is False


async def test_queued_cache_hit_survives_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "MAX_ENTRIES", 1)
    source = tmp_path / "speech.mp3"
    source.write_bytes(b"speech")
    queued = cache.cache_put(cache.CacheKey("hello", "", "elevenlabs"), source)
    assert queued is not None

    monkeypatch.setattr(PlaybackQueue, "_compute_timeout", AsyncMock(return_value=1.0))
    monkeypatch.setattr(
        PlaybackQueue, "_spawn_and_wait", AsyncMock(return_value=(0, 1.0, ""))
    )
    playback = PlaybackQueue()
    handler = _make_synthesize_handler(playback=playback)
    playing = asyncio.Event()
    ws = MagicMock()

    async def send(frame: dict[str, object]) -> None:
        if frame["type"] == "playing":
            assert frame["cached"] is True
            playing.set()

    ws.send_json = AsyncMock(side_effect=send)
    request = asyncio.create_task(
        handler(
            {
                "type": "synthesize",
                "id": "queued",
                "text": "hello",
                "provider": "elevenlabs",
            },
            ws,
        )
    )
    async with asyncio.timeout(2):
        try:
            await playing.wait()
            assert playback.queue_size == 1
            os.utime(queued, (1, 1))
            assert cache.cache_put(cache.CacheKey("later", "", "elevenlabs"), source)
            assert not queued.exists()
            consumer = asyncio.create_task(playback.consumer())
            try:
                await request
            finally:
                consumer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await consumer
        finally:
            request.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await request

    result = playback.last_result
    assert result is not None
    assert result.ok, result.failure_detail()
    assert not result.path.exists()


class TestDedupHitLogIsInjectionSafe:
    """The dedup-hit sink escapes the raw client id so it cannot forge a line."""

    @pytest.mark.asyncio
    async def test_newline_in_id_cannot_forge_a_log_line(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A newline in the client ``id`` renders as ``\\n`` at the dedup sink.

        ``request_id`` is a raw wire field; logged with ``%r`` an embedded
        newline stays inside quotes, so the "Dedup hit" record is a single
        physical line and cannot smuggle a forged second entry.
        """
        handler, _ = TestHandleSynthesizeOnceFlag._make_stubbed_handler(monkeypatch)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        first: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "hi",
            "provider": "elevenlabs",
            "once": 600,
        }
        forged: dict[str, object] = {
            "type": "synthesize",
            "id": "b\nFATAL forged entry",
            "text": "hi",
            "provider": "elevenlabs",
            "once": 600,
        }
        await handler(first, ws)
        with caplog.at_level(logging.INFO, logger="punt_vox.voxd"):
            await handler(forged, ws)

        hits = [r for r in caplog.records if "Dedup hit" in r.getMessage()]
        assert len(hits) == 1
        message = hits[0].getMessage()
        assert "\n" not in message, "the id newline must be escaped, not raw"
        assert "\\n" in message  # rendered visibly by %r
        assert message.splitlines() == [message]  # exactly one physical line


class TestSynthesizeHandlerTypedErrorRouting:
    """The F2/F3/F5 wire promise: diagnosable errors cross verbatim through
    :meth:`WireReply.error`, never through :meth:`WireReply.fault` (which
    launders the message to ``"operation failed"``).

    Each test drives the real ``SynthesizeHandler`` end-to-end -- parsing
    the wire message, entering the synthesis path, catching the typed
    error, sending the frame -- and asserts BOTH halves: the sentence
    reaches the peer, AND it arrives as an ``error`` frame with an
    ``id`` (the WARNING ``rejected op`` audit shape), never as a
    ``fault`` frame carrying ``"operation failed"``.

    Handler-level tests exercise the raise site as well as the catch clause.
    Testing an exception type in isolation cannot prove a handler actually
    raises it.
    """

    @staticmethod
    def _handler_that_raises(
        exc: Exception,
    ) -> tuple[SynthesizeHandler, list[dict[str, object]]]:
        """Build a SynthesizeHandler whose synthesize_to_file raises *exc*."""
        sent: list[dict[str, object]] = []

        async def _raising(*_a: object, **_k: object) -> SynthesisOutcome:
            raise exc

        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = _raising
        handler = _make_synthesize_handler(synthesis=mock_synth)
        return handler, sent

    @staticmethod
    async def _drive(
        handler: SynthesizeHandler,
    ) -> list[dict[str, object]]:
        """Send one wire message through *handler*; return the frames sent."""
        ws = MagicMock()
        sent: list[dict[str, object]] = []

        async def _capture(frame: dict[str, object]) -> None:
            sent.append(frame)

        ws.send_json = _capture
        await handler(
            {
                "type": "synthesize",
                "id": "req-1",
                "text": "hi",
                "provider": "elevenlabs",
            },
            ws,
        )
        return sent

    @pytest.mark.asyncio
    async def test_f2_provider_unavailable_crosses_verbatim_via_error(self) -> None:
        # F2: the daemon has no credentials for the named provider.
        # Wire frame must be type=error carrying the message verbatim,
        # NOT type=fault carrying "operation failed" -- the whole point
        # of the taxonomy is that a diagnosable failure reaches the
        # caller with a message they can act on.
        detail = (
            "provider 'polly' is configured but voxd has no AWS credentials "
            "(AWS_PROFILE, or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY); "
            "run `vox doctor`"
        )
        handler, _ = self._handler_that_raises(
            ProviderUnavailableError("polly", detail)
        )

        sent = await self._drive(handler)

        assert sent, "expected at least one frame on the wire"
        assert sent[-1]["type"] == "error"
        assert sent[-1]["message"] == detail
        assert not any(
            f.get("type") == "fault" or f.get("message") == "operation failed"
            for f in sent
        ), "F2 must never route through fault()"

    @pytest.mark.asyncio
    async def test_f3_provider_auth_crosses_verbatim_via_error(self) -> None:
        # F3: the credentials were present at the readiness check but
        # the provider rejected them at the SDK call. Same routing
        # promise as F2: through error(), not fault().
        handler, _ = self._handler_that_raises(ProviderAuthError("elevenlabs", 401))

        sent = await self._drive(handler)

        assert sent[-1]["type"] == "error"
        assert sent[-1]["message"] == (
            "provider 'elevenlabs' rejected the credentials (HTTP 401); "
            "run `vox doctor`"
        )
        assert not any(
            f.get("type") == "fault" or f.get("message") == "operation failed"
            for f in sent
        ), "F3 must never route through fault()"

    @pytest.mark.asyncio
    async def test_f4_unknown_provider_crosses_verbatim_via_error(self) -> None:
        # F4: a hand-edited ``vox.md`` naming ``provider: ploly``. The
        # registry raises ``UnknownProviderError`` (a ``ValueError``
        # subclass, so ``WireReply.reject_or_fault`` on the voices path
        # renders it verbatim). The synthesize handler catches it in
        # the same tuple as F2/F3/F5 -- widening to plain ValueError
        # would swallow genuine daemon-side bugs, which is why the
        # typed class exists. Without this catch, F4 falls through to
        # the broad except and reaches the caller as "operation
        # failed" -- the twin of the F3 case.
        handler, _ = self._handler_that_raises(
            UnknownProviderError("ploly", ["elevenlabs", "openai", "polly"])
        )

        sent = await self._drive(handler)

        assert sent[-1]["type"] == "error"
        assert sent[-1]["message"] == (
            "Unknown provider 'ploly'. Available: elevenlabs, openai, polly"
        )
        assert not any(
            f.get("type") == "fault" or f.get("message") == "operation failed"
            for f in sent
        ), "F4 must never route through fault()"

    @pytest.mark.asyncio
    async def test_f5_voice_not_found_crosses_verbatim_via_error(self) -> None:
        # The voice-not-found reason and available voices must cross the wire verbatim,
        # not become a generic operation-failed fault.
        handler, _ = self._handler_that_raises(
            VoiceNotFoundError("bella", ["alloy", "ash", "ballad"])
        )

        sent = await self._drive(handler)

        assert sent[-1]["type"] == "error"
        assert (
            sent[-1]["message"]
            == "Unknown voice 'bella'. Available: ['alloy', 'ash', 'ballad']"
        )
        assert not any(
            f.get("type") == "fault" or f.get("message") == "operation failed"
            for f in sent
        ), "F5 must never route through fault()"

    @pytest.mark.asyncio
    async def test_unusable_ca_bundle_crosses_verbatim_via_error(self) -> None:
        # An unloadable SSL_CERT_FILE is configuration the caller's operator
        # can fix; laundered to "operation failed" it is undiagnosable.
        message = (
            "SSL_CERT_FILE names a CA bundle that cannot be loaded "
            "(No such file or directory); fix the path or unset SSL_CERT_FILE"
        )
        handler, _ = self._handler_that_raises(CaBundleError(message))

        sent = await self._drive(handler)

        assert sent[-1]["type"] == "error"
        assert sent[-1]["message"] == message
        assert not any(
            f.get("type") == "fault" or f.get("message") == "operation failed"
            for f in sent
        ), "a CA bundle failure must never route through fault()"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "rejection",
        [
            ProviderAuthError("elevenlabs", 401),
            VoiceNotFoundError("bella", ["alloy"]),
        ],
    )
    async def test_synthesis_rejection_logs_once(
        self, rejection: ValueError, caplog: pytest.LogCaptureFixture
    ) -> None:
        handler, _ = self._handler_that_raises(rejection)

        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd"):
            sent = await self._drive(handler)

        records = [r for r in caplog.records if "req-1" in r.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert records[0].name == "punt_vox.voxd.wire_reply"
        assert str(rejection) not in records[0].getMessage()
        assert "hi" not in records[0].getMessage()
        assert sent[-1] == {"type": "error", "id": "req-1", "message": str(rejection)}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("direct_play", [True, False])
    async def test_local_rejection_logs_once_and_allows_retry(
        self, direct_play: bool, caplog: pytest.LogCaptureFixture
    ) -> None:
        rejection = ProviderAuthError("espeak", 401)
        synthesis = MagicMock(spec=SynthesisPipeline)
        synthesis.try_direct_play = AsyncMock(
            return_value=rejection if direct_play else None
        )
        synthesis.synthesize_to_file = AsyncMock(side_effect=rejection)
        dedup = OnceDedup()
        handler = _make_synthesize_handler(synthesis=synthesis, once_dedup=dedup)
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "local-rejection",
            "text": "private speech content",
            "provider": "espeak",
            "once": 30,
        }

        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd"):
            await handler(msg, websocket)

        records = [r for r in caplog.records if "local-rejection" in r.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "provider_auth" in records[0].getMessage()
        assert str(rejection) not in caplog.text
        assert "private speech content" not in caplog.text
        websocket.send_json.assert_awaited_once_with(
            {"type": "error", "id": "local-rejection", "message": str(rejection)}
        )
        assert dedup.check_and_record("private speech content", 30) is None

    @pytest.mark.asyncio
    async def test_reject_log_omits_caller_supplied_strings(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        handler, _ = self._handler_that_raises(
            VoiceNotFoundError("b\nFATAL forged audit line", ["alloy"])
        )

        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd"):
            await self._drive(handler)

        rejected = [r for r in caplog.records if "req-1" in r.getMessage()]
        assert len(rejected) == 1
        message = rejected[0].getMessage()
        assert "\n" not in message
        assert "forged audit line" not in caplog.text
        assert "alloy" not in caplog.text
        assert "voice_not_found" in message
        assert message.splitlines() == [message]

    @pytest.mark.asyncio
    async def test_unexpected_exception_still_routes_through_fault(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A genuine daemon-side bug (a KeyError deep in the pipeline, a
        # RuntimeError from an SDK we did not classify as auth) is not
        # a caller-side rejection and must still land on ``WireReply.fault``
        # with the laundered "operation failed" message -- the SafeFault
        # path exists to keep host paths off the wire. Both frame shapes
        # are ``type=error`` on the wire (that is what WireReply sends);
        # the caller-visible distinction is the ERROR audit line
        # containing "operation failed", NOT a WARNING "rejected op".
        # Asserting on the log line is the honest test: the message on
        # the wire is what the caller sees, and this asserts the wire
        # message did NOT preserve the internal detail.
        handler, _ = self._handler_that_raises(RuntimeError("unclassified"))

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd.wire_reply"):
            sent = await self._drive(handler)

        assert sent[-1]["type"] == "error"
        assert sent[-1]["message"] == "operation failed"
        # The wire message is laundered; the raw detail stays in the log.
        # Never a "rejected op" audit -- that would blame the client for
        # a daemon-side fault.
        assert any(
            r.levelno == logging.ERROR and "operation failed" in r.getMessage()
            for r in caplog.records
        )
        assert not any("rejected op" in r.getMessage() for r in caplog.records)
