"""Input receipts acknowledge delivery, not completion of a worker turn."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.websockets import WebSocket
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.contracts import WorkerInput
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.steering import WorkerSteering
from punt_vox.voxd.workers.steering_types import DecisionChoice
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.worker_client import WorkerClient

if TYPE_CHECKING:
    from punt_vox.voxd.workers.contracts import HarnessProfile
    from punt_vox.voxd.workers.types import WorkerPayload


@pytest.mark.parametrize(
    "advance", ["none", "before_roster", "after_roster", "cleared"]
)
@pytest.mark.parametrize("choice", [None, "approve", "deny"])
async def test_prompt_epoch_is_checked_under_delivery_lock_after_roster_lookup(
    tmp_path: Path,
    advance: str,
    choice: DecisionChoice | None,  # None tests an ordinary idle-text instruction.
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    store = HookContextStore()
    profile = ClaudeProfile(store)
    actor = WorkerActor(registry)
    hooks = actor.hooks(store)
    question: dict[str, object] = {
        "session_id": worker.id,
        "cwd": worker.worktree,
        "relay_seq": 3,
        "hook_event_name": "Notification",
        "notification_type": "permission_prompt"
        if choice is not None
        else "idle_prompt",
    }
    store.ingest(
        {**question, "relay_seq": 1, "hook_event_name": "SessionStart"},
        worker_input=True,
    )
    store.ingest(question, worker_input=True)
    original = WorkerActor.workers

    async def snapshot_then_next_prompt(self: WorkerActor) -> list[WorkerPayload]:
        if advance in ("before_roster", "cleared"):
            update = {**question, "relay_seq": 5}
            if advance == "cleared":
                update["hook_event_name"] = "PostToolUse"
            await hooks.ingest(update, worker_only=True)
        snapshot = await original(self)
        if advance == "after_roster":
            await hooks.ingest({**question, "relay_seq": 5}, worker_only=True)
        return snapshot

    def progress(worker_id: str, _harness: HarnessProfile) -> dict[str, object]:
        return profile.progress(worker_id)

    allowlist = MagicMock()
    allowlist.repos.return_value = (str(tmp_path),)
    with (
        patch.object(FakeWorkerProvider, "progress", side_effect=progress),
        patch.object(TrivialProfile, "last_input", side_effect=profile.last_input),
        patch.object(TrivialProfile, "inputs_since", side_effect=profile.inputs_since),
        patch.object(FakeWorkerProvider, "decide") as decide,
        patch.object(FakeWorkerProvider, "steer") as steer,
        patch.object(WorkerActor, "workers", new=snapshot_then_next_prompt),
        patch.object(WorkerSteering, "_receipt", return_value=None),
    ):
        result, error = await BossTools(actor, allowlist).answer(
            "steer_worker",
            {
                "worker_id": worker.id,
                "words": "Continue.",
                "choice": choice,
                "prompt_seq": 3 if choice is not None else None,
            },
        )
        rejected = advance == "after_roster" or (
            choice is not None and advance in ("before_roster", "cleared")
        )
        assert error == rejected
        if rejected:
            assert (
                "different prompt now" in result
                if choice is not None
                else "new prompt" in result
            )
            assert "ask the person again" in result
            decide.assert_not_called()
            steer.assert_not_called()
        else:
            assert decide.call_count == (choice is not None)
            assert steer.call_count == (choice is None)
    registry.stop(worker.id)


@pytest.mark.parametrize("epoch", [True, [], {}, 1.0, "unknown"])
async def test_invalid_observed_epoch_cannot_deliver(epoch: object) -> None:
    registry = MagicMock(spec=WorkerRegistry)
    with pytest.raises(ValueError):
        await WorkerSteering(registry, asyncio.Lock()).handle(
            {"worker_id": "w", "words": "Continue.", "prompt_epoch": epoch}
        )
    registry.steer.assert_not_called()


@pytest.mark.parametrize("choice", ["maybe", "", 1, [], {}])
async def test_invalid_choice_is_rejected_before_delivery(choice: object) -> None:
    registry = MagicMock(spec=WorkerRegistry)
    with pytest.raises(ValueError, match="choice must be approve or deny"):
        await WorkerSteering(registry, asyncio.Lock()).handle(
            {"worker_id": "w", "words": "answer", "choice": choice}
        )
    registry.steer.assert_not_called()


@pytest.mark.subprocess
def test_receipt_types_import_without_worker_implementations() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from punt_vox.voxd.workers.steering_types import SteerReceipt; "
                "import sys; "
                "assert SteerReceipt.__required_keys__ == "
                "{'worker_id', 'heard', 'receipt_seq', 'urgent', 'choice'}; "
                "assert 'punt_vox.voxd.workers.steering' not in sys.modules; "
                "assert 'punt_vox.voxd.workers.registry' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failure", ["load-buffer", "paste-buffer"])
def test_paste_failure_never_submits_and_cleans_loaded_buffer(failure: str) -> None:
    def run(*args: str, **kwargs: object) -> None:
        if args[0] == failure:
            raise RuntimeError("transport failed")

    with (
        patch.object(TmuxWorkerProvider, "_run", side_effect=run) as command,
        pytest.raises(RuntimeError, match="transport failed"),
    ):
        TmuxWorkerProvider().steer("w", "words", urgent=False, profile=TrivialProfile())
    commands = [call.args[0] for call in command.call_args_list]
    assert commands == (
        ["load-buffer"]
        if failure == "load-buffer"
        else ["load-buffer", "paste-buffer", "delete-buffer"]
    )


@pytest.mark.parametrize(
    "words", ["", "  ", "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb", "a\u2028b"]
)
def test_invalid_words_never_sent(tmp_path: Path, words: str) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    with pytest.raises(ValueError, match=r"empty|control"):
        registry.steer("missing", words)


@pytest.mark.parametrize("ready", [False, True])
def test_ready_only_and_baseline(tmp_path: Path, ready: bool) -> None:
    """Only ready workers steered: capture baseline before provider delivery."""
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

    def run(*args: str, **kwargs: object) -> None:
        calls.append((args, kwargs))

    def settle(delay: float) -> None:
        calls.append(delay)

    with (
        patch("punt_vox.voxd.workers.tmux.uuid4", return_value="input"),
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
        (("load-buffer", "-b", "vox-steer-input", "-"), {"input_text": "literal"}),
        (("paste-buffer", "-b", "vox-steer-input", "-t", "=w:"), {}),
        (("delete-buffer", "-b", "vox-steer-input"), {"check": False}),
        TrivialProfile.settle_delay,
        (("send-keys", "-t", "=w:", "Enter"), {}),
    ]
    if urgent:
        expected[:0] = [
            (("send-keys", "-t", "=w:", "Escape"), {}),
            TrivialProfile.settle_delay,
        ]
    assert calls == expected


@pytest.mark.parametrize("length", [500, 1500])
def test_profile_receipts_latest_prompt_and_clipping(
    tmp_path: Path, length: int
) -> None:
    """Receipt freshness and exact text exclude stale or clipped inputs."""
    store = HookContextStore()
    profile = ClaudeProfile(store)
    assert profile.last_input("w") is None
    assert profile.inputs_since("w", 0) == []
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    words = "x" * length

    def last_input(worker_id: str) -> WorkerInput | None:
        return profile.last_input("w")

    def inputs_since(worker_id: str, baseline: int) -> list[WorkerInput]:
        return profile.inputs_since("w", baseline)

    with (
        patch.object(TrivialProfile, "last_input", side_effect=last_input),
        patch.object(
            TrivialProfile,
            "inputs_since",
            side_effect=inputs_since,
        ),
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
                },
                worker_input=True,
            )
            expected = 7 if seq == 7 and length <= 1000 else None
            assert registry.receipt(worker.id, words, 5) == expected
        expected_text = words if length <= 1000 else "x" * 1000 + "..."
        assert profile.last_input("w") == WorkerInput(7, expected_text)
        store.ingest(
            {
                "session_id": "w",
                "hook_event_name": "UserPromptSubmit",
                "relay_seq": 8,
                "prompt": "",
            },
            worker_input=True,
        )
        assert profile.last_input("w") == WorkerInput(8, "")


def test_intermediate_input_remains_a_receipt_after_newer_prompt(
    tmp_path: Path,
) -> None:
    """Receipt freshness and exact text search retained inputs, not just the latest."""
    store = HookContextStore()
    profile = ClaudeProfile(store)
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    for sequence, text in [(5, "steered"), (6, "later unrelated prompt")]:
        store.ingest(
            {
                "session_id": worker.id,
                "hook_event_name": "UserPromptSubmit",
                "relay_seq": sequence,
                "prompt": text,
            },
            worker_input=True,
        )
    with (
        patch.object(TrivialProfile, "last_input", side_effect=profile.last_input),
        patch.object(TrivialProfile, "inputs_since", side_effect=profile.inputs_since),
        patch.object(
            TrivialProfile, "matches_input", side_effect=profile.matches_input
        ),
    ):
        assert registry.receipt(worker.id, "steered", 4) == 5
        assert registry.receipt(worker.id, "steered", 5) is None
        assert registry.receipt(worker.id, "absent", 0) is None


def test_profile_rejects_clipped_prefix_collision() -> None:
    profile = ClaudeProfile(HookContextStore())
    prefix = "x" * 1000
    assert not profile.matches_input(WorkerInput(5, prefix + "..."), prefix + "other")
    assert profile.matches_input(WorkerInput(5, prefix), prefix)


def test_profile_limit_checked_before_delivery(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    with (
        patch.object(TrivialProfile, "input_limit", 8),
        patch.object(FakeWorkerProvider, "steer") as send,
    ):
        with pytest.raises(ValueError, match=r"receipt limit.*8"):
            registry.steer(worker.id, "123456789")
        send.assert_not_called()
        registry.steer(worker.id, "12345678")
        assert send.call_args.args[1] == "12345678"


@pytest.mark.asyncio
async def test_same_worker_serialized_but_other_workers_remain_concurrent() -> None:
    """At most one steer in flight per worker, without blocking other workers."""
    registry = MagicMock(spec=WorkerRegistry)
    sequences: dict[str, int] = {}
    release_first = False

    def send(
        worker: str,
        words: str,
        *,
        urgent: bool,
        choice: object = None,
        prompt_epoch: object = None,  # None binds direct control to the live prompt.
        prompt_seq: object = None,  # None is ordinary text, not a permission answer.
    ) -> int:
        baseline = sequences.get(worker, 0)
        sequences[worker] = baseline + 1
        return baseline

    def receipt(worker: str, words: str, baseline: int) -> int | None:
        if worker == "one" and sequences[worker] == 1 and not release_first:
            return None
        return sequences[worker]

    registry.steer.side_effect = send
    registry.receipt.side_effect = receipt
    handlers = WorkerHandlers.build(registry)
    first_ws, second_ws, other_ws = (AsyncMock(spec=WebSocket) for _ in range(3))
    message: dict[str, object] = {
        "type": "worker_steer",
        "worker_id": "one",
        "words": "same words",
    }
    first = asyncio.create_task(handlers["worker_steer"](message, first_ws))
    async with asyncio.timeout(1):
        while not registry.receipt.called:
            await asyncio.sleep(0.001)
    second = asyncio.create_task(handlers["worker_steer"](message, second_ws))
    await asyncio.wait_for(
        handlers["worker_steer"]({**message, "worker_id": "two"}, other_ws), 1
    )
    try:
        assert sequences == {"one": 1, "two": 1}
    finally:
        release_first = True
        await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert first_ws.send_json.call_args.args[0]["result"]["receipt_seq"] == 1
    assert second_ws.send_json.call_args.args[0]["result"]["receipt_seq"] == 2
    assert other_ws.send_json.call_args.args[0]["result"]["heard"] is True


@pytest.mark.asyncio
async def test_queued_steer_times_out_without_delivery() -> None:
    registry = MagicMock(spec=WorkerRegistry)
    registry.steer.return_value = 4
    steering = WorkerSteering(registry, asyncio.Lock())
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def receipt(worker_id: str, words: str, baseline: int) -> int:
        waiting.set()
        await release.wait()
        return 5

    with (
        patch.object(WorkerSteering, "TIMEOUT", 0.01),
        patch.object(WorkerSteering, "_receipt", side_effect=receipt),
    ):
        first = asyncio.create_task(
            steering.handle({"worker_id": "one", "words": "first"})
        )
        await asyncio.wait_for(waiting.wait(), 1)
        try:
            with pytest.raises(ValueError, match="worker is busy with another steer"):
                await asyncio.wait_for(
                    steering.handle({"worker_id": "one", "words": "queued"}), 1
                )
            registry.steer.assert_called_once_with(
                "one",
                "first",
                urgent=False,
                choice=None,
                prompt_epoch=None,
                prompt_seq=None,
            )
        finally:
            release.set()
            await asyncio.wait_for(first, 1)
        registry.steer.assert_called_once_with(
            "one",
            "first",
            urgent=False,
            choice=None,
            prompt_epoch=None,
            prompt_seq=None,
        )
        response = await steering.handle({"worker_id": "one", "words": "after"})
        assert response["receipt_seq"] == 5
        assert [call.args[1] for call in registry.steer.call_args_list] == [
            "first",
            "after",
        ]


@pytest.mark.asyncio
async def test_no_delivery_after_deadline_while_actor_is_busy() -> None:
    """No delivery after deadline: a held actor cannot defer a stale steer."""
    registry = MagicMock(spec=WorkerRegistry)
    actor = asyncio.Lock()
    steering = WorkerSteering(registry, actor)
    async with actor:
        with (
            patch.object(WorkerSteering, "TIMEOUT", 0.01),
            pytest.raises(ValueError, match=r"busy|deadline"),
        ):
            await asyncio.wait_for(
                steering.handle({"worker_id": "one", "words": "stale", "urgent": True}),
                1,
            )
        registry.steer.assert_not_called()
    registry.steer.assert_not_called()
    registry.steer.return_value = 4
    registry.receipt.return_value = 5
    response = await asyncio.wait_for(
        steering.handle({"worker_id": "one", "words": "fresh"}), 1
    )
    assert response["heard"] is True
    registry.steer.assert_called_once_with(
        "one", "fresh", urgent=False, choice=None, prompt_epoch=None, prompt_seq=None
    )


@pytest.mark.asyncio
async def test_deadline_is_rechecked_after_the_actor_lock_is_acquired() -> None:
    registry = MagicMock(spec=WorkerRegistry)
    actor = asyncio.Lock()
    steering = WorkerSteering(registry, actor)

    async def acquire_late(lock: asyncio.Lock, deadline: float) -> None:
        del deadline
        await lock.acquire()

    with (
        patch.object(WorkerSteering, "_acquire", side_effect=acquire_late),
        pytest.raises(ValueError, match="steering deadline expired before delivery"),
    ):
        await steering._deliver(
            "worker",
            "Continue.",
            urgent=False,
            deadline=0,
            choice=None,
            prompt_epoch=None,
            prompt_seq=None,
        )
    registry.steer.assert_not_called()
    assert not actor.locked()


@pytest.mark.asyncio
async def test_no_delivery_after_deadline_uses_one_budget_for_both_locks() -> None:
    """No delivery after deadline: acquiring the worker never renews the budget."""
    registry = MagicMock(spec=WorkerRegistry)
    registry.steer.return_value = 4
    registry.receipt.return_value = 5
    steering = WorkerSteering(registry, asyncio.Lock())
    with patch.object(
        WorkerSteering, "_acquire", wraps=WorkerSteering._acquire
    ) as acquire:
        result = await steering.handle({"worker_id": "one", "words": "words"})
    assert result["heard"] is True
    assert acquire.call_count == 2
    assert acquire.call_args_list[0].args[1] == acquire.call_args_list[1].args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_failed_steer_releases_worker_lock(failure: str) -> None:
    registry = MagicMock(spec=WorkerRegistry)
    registry.steer.return_value = 4
    registry.receipt.return_value = None
    steering = WorkerSteering(registry, asyncio.Lock())
    message: dict[str, object] = {"worker_id": "one", "words": "words"}
    with patch.object(WorkerSteering, "TIMEOUT", 0.01):
        if failure == "error":
            registry.steer.side_effect = ValueError("not ready")
            with pytest.raises(ValueError, match="not ready"):
                await steering.handle(message)
            registry.steer.side_effect = None
        else:
            assert (await steering.handle(message))["heard"] is False
        registry.receipt.return_value = 5
        response = await asyncio.wait_for(steering.handle(message), 1)
        assert response["receipt_seq"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("heard", [True, False])
async def test_receipt_wait_releases_hook_ingest_lock(heard: bool) -> None:
    """Heard never implies turn ended; Timeout is an unheard success."""
    registry = MagicMock(spec=WorkerRegistry)
    registry.steer.return_value = 4
    registry.receipt.return_value = None
    store = HookContextStore()
    handler = WorkerHandlers(WorkerActor(registry))
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
            "choice": None,
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
    with patch("punt_vox.daemon_control.VoxdTransport", return_value=transport):
        assert await WorkerClient.request(
            "steer", worker_id="w", words="literal", urgent=True
        ) == {"heard": False}
    assert transport.send_and_recv.call_args.args[0]["urgent"] is True
    assert transport.send_and_recv.call_args.kwargs["timeout"] == 30
