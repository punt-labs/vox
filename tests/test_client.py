"""Tests for punt_vox.client -- WebSocket client for voxd."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from punt_vox.client import (
    VoxClient,
    VoxClientSync,
    VoxdConnectionError,
    VoxdProtocolError,
    _run_dir,  # pyright: ignore[reportPrivateUsage]
    read_port_file,
    read_token_file,
)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_run_dir_is_user_state() -> None:
    """_run_dir is ``~/.punt-labs/vox/run`` — same on macOS and Linux."""
    assert _run_dir() == Path.home() / ".punt-labs" / "vox" / "run"


# ---------------------------------------------------------------------------
# Port / token file readers
# ---------------------------------------------------------------------------


def test_read_port_file(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    port_file.write_text("9999")
    with patch("punt_vox.client._run_dir", return_value=tmp_path):
        assert read_port_file() == 9999


def test_read_port_file_missing(tmp_path: Path) -> None:
    with patch("punt_vox.client._run_dir", return_value=tmp_path):
        assert read_port_file() is None


def test_read_token_file(tmp_path: Path) -> None:
    token_file = tmp_path / "serve.token"
    token_file.write_text("secret123")
    with patch("punt_vox.client._run_dir", return_value=tmp_path):
        assert read_token_file() == "secret123"


def test_read_token_file_missing(tmp_path: Path) -> None:
    with patch("punt_vox.client._run_dir", return_value=tmp_path):
        assert read_token_file() is None


# ---------------------------------------------------------------------------
# VoxClient -- unit tests (mock WebSocket)
# ---------------------------------------------------------------------------


def _make_mock_ws() -> AsyncMock:
    """Create an AsyncMock that behaves like a websockets connection."""
    ws = AsyncMock()
    ws.close = AsyncMock()
    ws.send = AsyncMock()
    ws.ping = AsyncMock()
    return ws


class TestVoxClientConnect:
    """Test connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        mock_ws = _make_mock_ws()
        with patch(
            "punt_vox.client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = VoxClient(port=8421, token="tok")
            await client.connect()
            assert client._ws is mock_ws  # pyright: ignore[reportPrivateUsage]
            await client.close()
            mock_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_refused_raises(self) -> None:
        with patch(
            "punt_vox.client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            side_effect=OSError("Connection refused"),
        ):
            client = VoxClient(port=8421, token="tok")
            with pytest.raises(VoxdConnectionError, match="Cannot connect"):
                await client.connect()

    @pytest.mark.asyncio
    async def test_connect_reads_port_file(self, tmp_path: Path) -> None:
        port_file = tmp_path / "serve.port"
        port_file.write_text("9999")
        token_file = tmp_path / "serve.token"
        token_file.write_text("mytoken")

        mock_ws = _make_mock_ws()
        with (
            patch("punt_vox.client._run_dir", return_value=tmp_path),
            patch(
                "punt_vox.client.websockets.asyncio.client.connect",
                new_callable=AsyncMock,
                return_value=mock_ws,
            ) as mock_connect,
        ):
            client = VoxClient()  # no port/token args
            await client.connect()
            call_args = mock_connect.call_args
            uri = call_args[0][0]
            assert "9999" in uri
            assert "token=mytoken" in uri
            await client.close()

    @pytest.mark.asyncio
    async def test_connect_no_port_file_raises(self, tmp_path: Path) -> None:
        with patch("punt_vox.client._run_dir", return_value=tmp_path):
            client = VoxClient()
            with pytest.raises(VoxdConnectionError, match="port file not found"):
                await client.connect()


class TestVoxClientBuildUri:
    """Test URI construction."""

    def test_uri_with_token(self) -> None:
        client = VoxClient(port=8421, token="abc")
        uri = client._build_uri()  # pyright: ignore[reportPrivateUsage]
        assert uri == "ws://127.0.0.1:8421/ws?token=abc"

    def test_uri_without_token(self) -> None:
        with patch("punt_vox.client.read_token_file", return_value=None):
            client = VoxClient(port=8421)
            uri = client._build_uri()  # pyright: ignore[reportPrivateUsage]
            assert uri == "ws://127.0.0.1:8421/ws"

    def test_uri_custom_host(self) -> None:
        client = VoxClient(host="10.0.0.1", port=9000, token="t")
        uri = client._build_uri()  # pyright: ignore[reportPrivateUsage]
        assert uri == "ws://10.0.0.1:9000/ws?token=t"


class TestVoxClientSynthesize:
    """Test synthesize method."""

    @pytest.mark.asyncio
    async def test_synthesize_returns_request_id(self) -> None:
        mock_ws = _make_mock_ws()
        # Server sends "playing" then "done".
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps({"type": "playing", "id": "req1"}),
                json.dumps({"type": "done", "id": "req1"}),
            ]
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        result = await client.synthesize("Hello world")
        assert isinstance(result.request_id, str)
        assert len(result.request_id) == 12
        assert result.deduped is False
        assert result.original_played_at is None
        assert result.ttl_seconds_remaining is None

        # Verify the message sent to the server.
        sent_raw = mock_ws.send.call_args[0][0]
        sent = json.loads(sent_raw)
        assert sent["type"] == "synthesize"
        assert sent["text"] == "Hello world"
        assert sent["rate"] == 90

    @pytest.mark.asyncio
    async def test_synthesize_with_all_params(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps({"type": "playing", "id": "x"}),
                json.dumps({"type": "done", "id": "x"}),
            ]
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        await client.synthesize(
            "Test",
            voice="drew",
            provider="elevenlabs",
            model="eleven_turbo_v2_5",
            rate=100,
            language="en",
            vibe_tags="calm",
            stability=0.5,
            similarity=0.8,
            style=0.3,
            speaker_boost=True,
            api_key="sk-test",
        )

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["voice"] == "drew"
        assert sent["provider"] == "elevenlabs"
        assert sent["model"] == "eleven_turbo_v2_5"
        assert sent["rate"] == 100
        assert sent["language"] == "en"
        assert sent["vibe_tags"] == "calm"
        assert sent["stability"] == 0.5
        assert sent["similarity"] == 0.8
        assert sent["style"] == 0.3
        assert sent["speaker_boost"] is True
        assert sent["api_key"] == "sk-test"

    @pytest.mark.asyncio
    async def test_synthesize_error_raises(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {"type": "error", "id": "x", "message": "empty text"}
            )
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(VoxdProtocolError, match="empty text"):
            await client.synthesize("")

    @pytest.mark.asyncio
    async def test_synthesize_omits_none_params(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps({"type": "done", "id": "x"}),
            ]
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        await client.synthesize("Hello")
        sent = json.loads(mock_ws.send.call_args[0][0])
        # None params should not appear in the message.
        assert "voice" not in sent
        assert "provider" not in sent
        assert "model" not in sent
        assert "language" not in sent
        assert "vibe_tags" not in sent
        assert "stability" not in sent
        assert "similarity" not in sent
        assert "style" not in sent
        assert "speaker_boost" not in sent
        assert "api_key" not in sent


class TestVoxClientChime:
    """Test chime method."""

    @pytest.mark.asyncio
    async def test_chime(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps({"type": "playing", "id": "chime:done"}),
                json.dumps({"type": "done", "id": "chime:done"}),
            ]
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        await client.chime("done")
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent == {"type": "chime", "signal": "done"}

    @pytest.mark.asyncio
    async def test_chime_error_raises(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {"type": "error", "id": "", "message": "unknown chime: bad"}
            )
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(VoxdProtocolError, match="unknown chime"):
            await client.chime("bad")


class TestVoxClientRecord:
    """Test record method."""

    @pytest.mark.asyncio
    async def test_record_returns_bytes(self) -> None:
        audio_bytes = b"\xff\xfb\x90\x00" * 10
        encoded = base64.b64encode(audio_bytes).decode("ascii")

        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps({"type": "audio", "id": "r1", "data": encoded})
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        result = await client.record("Hello")
        assert result == audio_bytes

    @pytest.mark.asyncio
    async def test_record_unexpected_type_raises(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(return_value=json.dumps({"type": "done", "id": "r1"}))
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(VoxdProtocolError, match="Expected 'audio'"):
            await client.record("Hello")


class TestVoxClientVoices:
    """Test voices method."""

    @pytest.mark.asyncio
    async def test_voices(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "type": "voices",
                    "provider": "elevenlabs",
                    "voices": ["drew", "matilda"],
                }
            )
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        result = await client.voices(provider="elevenlabs")
        assert result == ["drew", "matilda"]

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["provider"] == "elevenlabs"

    @pytest.mark.asyncio
    async def test_voices_no_provider(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {"type": "voices", "provider": "say", "voices": ["fred"]}
            )
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        result = await client.voices()
        assert result == ["fred"]
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert "provider" not in sent


class TestVoxClientHealth:
    """Test health method."""

    @pytest.mark.asyncio
    async def test_health(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "type": "health",
                    "status": "ok",
                    "uptime_seconds": 42.5,
                    "queued": 0,
                }
            )
        )
        client = VoxClient(port=8421, token="tok")
        client._ws = mock_ws  # pyright: ignore[reportPrivateUsage]

        result = await client.health()
        assert result["status"] == "ok"
        assert result["uptime_seconds"] == 42.5


class TestVoxClientReconnect:
    """Test automatic reconnection."""

    @pytest.mark.asyncio
    async def test_reconnect_on_dead_connection(self) -> None:
        mock_ws_old = _make_mock_ws()
        mock_ws_old.ping = AsyncMock(side_effect=Exception("closed"))

        mock_ws_new = _make_mock_ws()
        mock_ws_new.recv = AsyncMock(
            return_value=json.dumps(
                {"type": "health", "status": "ok", "uptime_seconds": 1.0, "queued": 0}
            )
        )

        with (
            patch("punt_vox.client.read_port_file", return_value=8421),
            patch("punt_vox.client.read_token_file", return_value="tok"),
            patch(
                "punt_vox.client.websockets.asyncio.client.connect",
                new_callable=AsyncMock,
                return_value=mock_ws_new,
            ),
        ):
            client = VoxClient(port=8421, token="tok")
            client._ws = mock_ws_old  # pyright: ignore[reportPrivateUsage]

            result = await client.health()
            assert result["status"] == "ok"
            assert client._ws is mock_ws_new  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# VoxClientSync
# ---------------------------------------------------------------------------


class TestVoxClientSync:
    """Test synchronous wrapper."""

    def test_health(self) -> None:
        health_resp = {
            "type": "health",
            "status": "ok",
            "uptime_seconds": 10.0,
            "queued": 0,
        }

        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(return_value=json.dumps(health_resp))

        with patch(
            "punt_vox.client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            sync_client = VoxClientSync(port=8421, token="tok")
            result = sync_client.health()
            assert result["status"] == "ok"

    def test_synthesize(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps({"type": "playing", "id": "x"}),
                json.dumps({"type": "done", "id": "x"}),
            ]
        )

        with patch(
            "punt_vox.client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            sync_client = VoxClientSync(port=8421, token="tok")
            result = sync_client.synthesize("Hello")
            assert isinstance(result.request_id, str)
            assert result.deduped is False

    def test_chime(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps({"type": "playing", "id": "chime:done"}),
                json.dumps({"type": "done", "id": "chime:done"}),
            ]
        )

        with patch(
            "punt_vox.client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            sync_client = VoxClientSync(port=8421, token="tok")
            sync_client.chime("done")

    def test_record(self) -> None:
        audio_bytes = b"\xff\xfb\x90\x00" * 5
        encoded = base64.b64encode(audio_bytes).decode("ascii")

        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps({"type": "audio", "id": "r1", "data": encoded})
        )

        with patch(
            "punt_vox.client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            sync_client = VoxClientSync(port=8421, token="tok")
            result = sync_client.record("Hello")
            assert result == audio_bytes

    def test_voices(self) -> None:
        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {"type": "voices", "provider": "say", "voices": ["fred"]}
            )
        )

        with patch(
            "punt_vox.client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            sync_client = VoxClientSync(port=8421, token="tok")
            result = sync_client.voices()
            assert result == ["fred"]
