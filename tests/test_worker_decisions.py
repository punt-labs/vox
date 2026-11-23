from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.voice_call.progress import ProgressEvents
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.contracts import WorkerInput
from punt_vox.voxd.workers.hooks import WorkerHooks


def test_pending_decision_order_clear_and_receipt() -> None:
    store = HookContextStore()
    profile = ClaudeProfile(store)

    def ingest(seq: int, event: str, **fields: object) -> None:
        store.ingest(
            {"session_id": "w", "hook_event_name": event, "relay_seq": seq, **fields},
            worker_input=True,
        )

    ingest(1, "UserPromptSubmit", prompt="start")
    ingest(3, "Notification", notification_type="permission_prompt", message="Allow?")
    decision = store.snapshot("w")["pending_decision"]
    assert decision == {
        "seq": 3,
        "notification_type": "permission_prompt",
        "message": "Allow?",
    }
    baseline = profile.last_input("w")
    assert baseline is not None and baseline.seq == 3
    ingest(2, "PreToolUse")
    assert store.snapshot("w")["pending_decision"] == decision
    ingest(4, "Notification", notification_type="other")
    assert store.snapshot("w")["pending_decision"] == decision
    ingest(5, "PostToolUse")
    assert store.snapshot("w")["pending_decision"] is None
    receipts = profile.inputs_since("w", baseline.seq)
    assert [receipt.seq for receipt in receipts] == [4, 5]
    assert profile.inputs_since("w", 4) == [WorkerInput(5, "", decision=True)]
    assert profile.matches_input(receipts[0], "yes, allow it")
    assert profile.inputs_since("w", 1) == []


def test_new_decision_notification_is_also_a_receipt() -> None:
    store = HookContextStore()
    profile = ClaudeProfile(store)
    notification: dict[str, object] = {
        "session_id": "w",
        "relay_seq": 2,
        "hook_event_name": "Notification",
        "notification_type": "permission_prompt",
        "message": "Allow?",
    }
    store.ingest(notification, worker_input=True)
    store.ingest({**notification, "relay_seq": 4}, worker_input=True)
    assert profile.inputs_since("w", 2) == [WorkerInput(4, "", decision=True)]
    assert profile.last_input("w") == WorkerInput(4, "", decision=True)


async def test_admitted_publication_deduplicated(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    events = ProgressEvents()
    store = HookContextStore()
    hooks = WorkerHooks(store, registry, asyncio.Lock(), events)
    payload: dict[str, object] = {
        "session_id": worker.id,
        "cwd": worker.worktree,
        "relay_seq": 1,
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "message": "Need input",
    }
    async with events.subscribe() as queue:
        await hooks.ingest({**payload, "cwd": "/unknown"}, worker_only=True)
        assert queue.empty()
        await hooks.ingest(payload, worker_only=True)
        event = queue.get_nowait()
        assert (
            event.kind == "needs_decision" and event.notification_type == "idle_prompt"
        )
        await hooks.ingest(payload, worker_only=True)
        assert queue.empty()
        await hooks.ingest(
            {
                **payload,
                "relay_seq": 2,
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
            },
            worker_only=True,
        )
        assert queue.get_nowait().text == "done"
        with pytest.raises(ValueError, match="relay_seq"):
            await hooks.ingest({**payload, "relay_seq": 0}, worker_only=True)
        assert queue.empty()
        await hooks.ingest(
            {**payload, "relay_seq": 3, "hook_event_name": "Stop"}, worker_only=True
        )
        assert queue.get_nowait().text == ""
        await hooks.ingest(
            {**payload, "relay_seq": 5, "hook_event_name": "PostToolUse"},
            worker_only=True,
        )
        late = {
            **payload,
            "relay_seq": 4,
            "hook_event_name": "Stop",
            "last_assistant_message": "late result",
        }
        await hooks.ingest(late, worker_only=True)
        assert queue.get_nowait().text == "late result"
        await hooks.ingest(late, worker_only=True)
        assert queue.empty()
        retry = {**payload, "relay_seq": 6}
        with (
            patch.object(
                type(registry),
                "checkpoint_progress",
                side_effect=OSError("disk failed"),
            ),
            pytest.raises(OSError, match="disk failed"),
        ):
            await hooks.ingest(retry, worker_only=True)
        assert queue.empty()
        await hooks.ingest(retry, worker_only=True)
        assert queue.get_nowait().seq == 6
        await hooks.ingest(retry, worker_only=True)
        assert queue.empty()


def test_registry_hears_pending_dialog_without_user_prompt(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    store = HookContextStore()
    profile = ClaudeProfile(store)
    payload: dict[str, object] = {
        "session_id": worker.id,
        "relay_seq": 3,
        "hook_event_name": "Notification",
        "notification_type": "permission_prompt",
        "message": "Allow?",
    }
    store.ingest(payload, worker_input=True)
    with (
        patch.object(TrivialProfile, "last_input", side_effect=profile.last_input),
        patch.object(TrivialProfile, "inputs_since", side_effect=profile.inputs_since),
        patch.object(
            TrivialProfile, "matches_input", side_effect=profile.matches_input
        ),
    ):
        baseline = registry.steer(worker.id, "Yes, allow it.")
        assert baseline == 3
        assert registry.receipt(worker.id, "Yes, allow it.", baseline) is None
        store.ingest(
            {**payload, "relay_seq": 5, "hook_event_name": "PostToolUse"},
            worker_input=True,
        )
        assert registry.receipt(worker.id, "Yes, allow it.", baseline) == 5
        assert store.last_input(worker.id) == (0, "")


async def test_delayed_decision_does_not_publish_an_obsolete_question(
    tmp_path: Path,
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    events = ProgressEvents()
    store = HookContextStore()
    hooks = WorkerHooks(store, registry, asyncio.Lock(), events)
    notification: dict[str, object] = {
        "session_id": worker.id,
        "cwd": worker.worktree,
        "hook_event_name": "Notification",
        "notification_type": "permission_prompt",
        "message": "Allow?",
        "relay_seq": 2,
    }
    async with events.subscribe() as queue:
        await hooks.ingest(
            {**notification, "relay_seq": 3, "hook_event_name": "PostToolUse"},
            worker_only=True,
        )
        await hooks.ingest(notification, worker_only=True)
        assert queue.empty()
        assert store.snapshot(worker.id)["pending_decision"] is None
        await hooks.ingest({**notification, "relay_seq": 4}, worker_only=True)
        assert queue.get_nowait().kind == "needs_decision"
        await hooks.ingest(notification, worker_only=True)
        assert queue.empty()
