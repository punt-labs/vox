"""Input receipts acknowledge delivery, not completion of a worker turn."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.websockets import WebSocket
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.contracts import WorkerInput
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.steering import WorkerSteering
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.worker_client import WorkerClient


@pytest.mark.parametrize(
    "words", ["", "  ", "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb", "a\u2028b"]
)
def test_invalid_words_never_sent(tmp_path: Path, words: str) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    with pytest.raises(ValueError, match=r"empty|control"):
        registry.steer("missing", words)


@pytest.mark.parametrize("ready", [False, True])
def test_ready_only_and_baseline(tmp_path: Path, ready: bool) -> None:
    provider = FakeWorkerProvider()
    provider.ready = ready
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    with patch.object(TrivialProfile, "last_input", return_value=WorkerInput(7, "old")):
        if ready:
            assert registry.steer(worker.id, "  literal words  ") == 7
        else:
            with pytest.raises(ValueError, match="not ready"):
                registry.steer(worker.id, "words")
    registry.stop(worker.id)
    with pytest.raises(ValueError, match="not ready"):
        registry.steer(worker.id, "words")


@pytest.mark.parametrize("urgent", [False, True])
def test_settle_order(urgent: bool) -> None:
    calls: list[object] = []

    def run(*args: str) -> None:
        calls.append(args)

    def settle(delay: float) -> None:
        calls.append(delay)

    with (
        patch.object(TmuxWorkerProvider, "_run", side_effect=run),
        patch(
            "punt_vox.voxd.workers.tmux.time.sleep",
            side_effect=settle,
        ),
    ):
        TmuxWorkerProvider().steer(
            "w", "literal", urgent=urgent, profile=TrivialProfile()
        )
    expected: list[object] = [
        ("send-keys", "-t", "=w:", "-l", "--", "literal"),
        TrivialProfile.settle_delay,
        ("send-keys", "-t", "=w:", "Enter"),
    ]
    if urgent:
        expected[:0] = [
            ("send-keys", "-t", "=w:", "Escape"),
            TrivialProfile.settle_delay,
        ]
    assert calls == expected


def test_profile_receipts_latest_prompt_and_clipping(tmp_path: Path) -> None:
    store = HookContextStore()
    profile = ClaudeProfile(store)
    assert profile.last_input("w") is None
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    words = "x" * 1500

    def last_input(worker_id: str) -> WorkerInput | None:
        return profile.last_input("w")

    with (
        patch.object(TrivialProfile, "last_input", side_effect=last_input),
        patch.object(
            TrivialProfile, "matches_input", side_effect=profile.matches_input
        ),
    ):
        for seq, text in [(5, words), (3, "stale"), (6, "different"), (7, words)]:
            store.ingest(
                {
                    "session_id": "w",
                    "hook_event_name": "UserPromptSubmit",
                    "relay_seq": seq,
                    "prompt": text,
                }
            )
            assert registry.receipt(worker.id, words, 5) == (7 if seq == 7 else None)
        assert profile.last_input("w") == WorkerInput(7, "x" * 1000 + "...")
        store.ingest(
            {
                "session_id": "w",
                "hook_event_name": "UserPromptSubmit",
                "relay_seq": 8,
                "prompt": "",
            }
        )
        assert profile.last_input("w") == WorkerInput(8, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("heard", [True, False])
async def test_receipt_wait_releases_hook_ingest_lock(heard: bool) -> None:
    registry = MagicMock(spec=WorkerRegistry)
    registry.steer.return_value = 4
    registry.receipt.return_value = None
    store = HookContextStore()
    handler = WorkerHandlers(registry)
    ws = AsyncMock(spec=WebSocket)
    handlers = WorkerHandlers.build(registry, store)
    hook_ws = AsyncMock(spec=WebSocket)
    with patch.object(WorkerSteering, "TIMEOUT", 0.15):
        task = asyncio.create_task(
            handlers["worker_steer"](
                {
                    "type": "worker_steer",
                    "worker_id": "w",
                    "words": "words",
                    "urgent": True,
                },
                ws,
            )
        )
        while not registry.receipt.called:
            await asyncio.sleep(0.001)
        await asyncio.wait_for(
            handlers["hook_event"](
                {
                    "payload": {
                        "session_id": "w",
                        "hook_event_name": "UserPromptSubmit",
                        "relay_seq": 5,
                        "prompt": "words",
                    }
                },
                hook_ws,
            ),
            timeout=0.1,
        )
        if heard:
            registry.receipt.return_value = 5
        await task
        assert ws.send_json.call_args.args[0]["result"] == {
            "worker_id": "w",
            "heard": heard,
            "receipt_seq": 5 if heard else None,
            "urgent": True,
        }
    assert store.last_input("w") == (5, "words")
    assert hook_ws.send_json.call_args.args[0]["type"] == "hook_event_accepted"
    await handler.handle(
        {"type": "worker_steer", "worker_id": "w", "words": "words", "urgent": "yes"},
        ws,
    )
    assert "bool" in ws.send_json.call_args.args[0]["message"]


@pytest.mark.asyncio
async def test_client_preserves_boolean() -> None:
    transport = AsyncMock()

    def reply(msg: dict[str, object], **kwargs: object) -> dict[str, object]:
        return {"type": "worker_result", "id": msg["id"], "result": {"heard": False}}

    transport.send_and_recv.side_effect = reply
    with patch("punt_vox.worker_client.VoxdTransport", return_value=transport):
        assert await WorkerClient.request(
            "steer", worker_id="w", words="literal", urgent=True
        ) == {"heard": False}
    assert transport.send_and_recv.call_args.args[0]["urgent"] is True
    assert transport.send_and_recv.call_args.kwargs["timeout"] == 30
