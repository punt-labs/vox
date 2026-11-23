from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.voice_agent.session import Conversation
from punt_vox.voxd.voice_call.progress import CallProgress, ProgressEvents
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.contracts import WorkerInput
from punt_vox.voxd.workers.hooks import WorkerHooks
from punt_vox.voxd.workers.steering_types import DecisionChoice
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider


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
        assert queue.empty()
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


@pytest.mark.parametrize(
    "prior_sequence,worker_only", [(8, True), (4, False), (8, False)]
)
@pytest.mark.parametrize(
    "hook,kind", [("Stop", "finished"), ("Notification", "needs_decision")]
)
async def test_unadmitted_context_cannot_suppress_worker_progress(
    tmp_path: Path, prior_sequence: int, worker_only: bool, hook: str, kind: str
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    events = ProgressEvents()
    store = HookContextStore()
    hooks = WorkerHooks(store, registry, asyncio.Lock(), events)
    unadmitted: dict[str, object] = {
        "session_id": worker.id,
        "cwd": str(tmp_path / "not-the-worktree"),
        "relay_seq": prior_sequence,
        "hook_event_name": "PostToolUse",
    }
    admitted = {
        **unadmitted,
        "cwd": worker.worktree,
        "relay_seq": 6,
        "hook_event_name": hook,
        "last_assistant_message": "valid result",
        "notification_type": "idle_prompt",
        "message": "Follow-up?",
    }
    assert not registry.admits_hook(unadmitted["session_id"], unadmitted["cwd"])
    assert registry.admits_hook(admitted["session_id"], admitted["cwd"])
    async with events.subscribe() as queue:
        await hooks.ingest(unadmitted, worker_only=worker_only)
        assert queue.empty()
        await hooks.ingest(admitted, worker_only=True)
        report = queue.get_nowait()
        assert (report.seq, report.kind) == (6, kind)
        assert store.worker_sequence(worker.id) == 6
        await hooks.ingest(admitted, worker_only=True)
        assert queue.empty()


async def test_failed_checkpoint_keeps_admitted_freshness_separate_from_delivery(
    tmp_path: Path,
) -> None:
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
        "relay_seq": 8,
        "hook_event_name": "PostToolUse",
    }
    async with events.subscribe() as queue:
        with (
            patch.object(
                type(registry),
                "checkpoint_progress",
                side_effect=OSError("disk failed"),
            ),
            pytest.raises(OSError, match="disk failed"),
        ):
            await hooks.ingest(payload, worker_only=True)
        assert store.worker_sequence(worker.id) == 8
        await hooks.ingest(
            {**payload, "relay_seq": 6, "hook_event_name": "Stop"}, worker_only=True
        )
        assert queue.empty()


@pytest.mark.parametrize("choice", ["approve", "deny"])
def test_registry_hears_pending_dialog_without_user_prompt(
    tmp_path: Path, choice: DecisionChoice
) -> None:
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
        patch.object(
            FakeWorkerProvider, "progress", return_value=dict(store.snapshot(worker.id))
        ),
        patch.object(TrivialProfile, "last_input", side_effect=profile.last_input),
        patch.object(TrivialProfile, "inputs_since", side_effect=profile.inputs_since),
        patch.object(
            TrivialProfile, "matches_input", side_effect=profile.matches_input
        ),
    ):
        baseline = registry.steer(
            worker.id, "The person's answer", choice=choice, prompt_seq=3
        )
        assert baseline == 3
        assert registry.receipt(worker.id, "Yes, allow it.", baseline) is None
        store.ingest(
            {**payload, "relay_seq": 5, "hook_event_name": "PostToolUse"},
            worker_input=True,
        )
        assert registry.receipt(worker.id, "Yes, allow it.", baseline) == 5
        assert store.last_input(worker.id) == (0, "")


@pytest.mark.parametrize("choice", ["approve", "deny"])
def test_choice_keys_never_paste_words_or_confirm_highlighted_yes(
    choice: DecisionChoice,
) -> None:
    profile = ClaudeProfile(HookContextStore())
    with patch.object(TmuxWorkerProvider, "_run") as command:
        TmuxWorkerProvider().decide("worker", choice, profile=profile)
    command.assert_called_once_with(
        "send-keys", "-t", "=worker:", "1" if choice == "approve" else "Escape"
    )
    assert "Enter" not in profile.decision_keys(choice)


@pytest.mark.parametrize(
    "pending,choice,urgent,error",
    [
        (True, None, False, "Pending decision: ask yes/no and pass choice=approve"),
        (False, "approve", False, "worker has a different prompt now"),
        (False, "deny", False, "worker has a different prompt now"),
        (True, "approve", True, "must not interrupt the permission dialog"),
        (True, "deny", True, "must not interrupt the permission dialog"),
    ],
)
def test_mismatched_decision_requests_never_touch_the_transport(
    tmp_path: Path,
    pending: bool,
    choice: DecisionChoice | None,  # None tests refusal of free text while blocked.
    urgent: bool,
    error: str,
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    report: dict[str, object] = {"session_started": True}
    if pending:
        report["pending_decision"] = {
            "seq": 3,
            "notification_type": "permission_prompt",
        }
    with (
        patch.object(FakeWorkerProvider, "progress", return_value=report),
        patch.object(
            TrivialProfile,
            "last_input",
            return_value=WorkerInput(3, "", decision=pending),
        ),
        patch.object(FakeWorkerProvider, "steer") as text,
        patch.object(FakeWorkerProvider, "decide") as decision,
        pytest.raises(ValueError, match=error),
    ):
        registry.steer(
            worker.id, "No, don't run that", choice=choice, urgent=urgent, prompt_seq=3
        )
    text.assert_not_called()
    decision.assert_not_called()


@pytest.mark.parametrize("prompt_seq", [None, True, False, 0, -1])
def test_missing_permission_sequence_never_touches_the_transport(
    tmp_path: Path,
    prompt_seq: int | None,  # None exercises the missing permission sequence.
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    with (
        patch.object(FakeWorkerProvider, "steer") as text,
        patch.object(FakeWorkerProvider, "decide") as decision,
        pytest.raises(ValueError, match="choice requires prompt_seq"),
    ):
        registry.steer(worker.id, "Yes", choice="approve", prompt_seq=prompt_seq)
    text.assert_not_called()
    decision.assert_not_called()


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


def test_idle_prompt_accepts_followup_text_and_requires_a_prompt_receipt(
    tmp_path: Path,
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    store = HookContextStore()
    profile = ClaudeProfile(store)
    event: dict[str, object] = {
        "session_id": worker.id,
        "hook_event_name": "UserPromptSubmit",
        "relay_seq": 1,
        "prompt": "task",
    }
    store.ingest(event, worker_input=True)
    store.ingest(
        {
            **event,
            "relay_seq": 2,
            "hook_event_name": "Notification",
            "notification_type": "idle_prompt",
        },
        worker_input=True,
    )
    assert store.snapshot(worker.id)["pending_decision"] is not None
    assert profile.last_input(worker.id) == WorkerInput(1, "task")
    with (
        patch.object(TrivialProfile, "last_input", side_effect=profile.last_input),
        patch.object(TrivialProfile, "inputs_since", side_effect=profile.inputs_since),
        patch.object(
            TrivialProfile, "matches_input", side_effect=profile.matches_input
        ),
        patch.object(FakeWorkerProvider, "steer") as text,
        patch.object(FakeWorkerProvider, "decide") as decision,
    ):
        for choice in ("approve", "deny"):
            with pytest.raises(ValueError, match="choice requires"):
                registry.steer(worker.id, "answer", choice=choice)
        baseline = registry.steer(worker.id, "followup")
        text.assert_called_once()
        decision.assert_not_called()
        store.ingest(
            {**event, "relay_seq": 3, "hook_event_name": "PostToolUse"},
            worker_input=True,
        )
        assert registry.receipt(worker.id, "followup", baseline) is None
        store.ingest({**event, "relay_seq": 4, "prompt": "followup"}, worker_input=True)
        assert registry.receipt(worker.id, "followup", baseline) == 4
        assert store.snapshot(worker.id)["pending_decision"] is None


async def test_superseded_stop_is_silent_and_preserves_a_queued_result(
    tmp_path: Path,
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    events = ProgressEvents()
    hooks = WorkerHooks(HookContextStore(), registry, asyncio.Lock(), events)
    conversation = AsyncMock(spec=Conversation)
    payload: dict[str, object] = {
        "session_id": worker.id,
        "cwd": worker.worktree,
        "hook_event_name": "Stop",
        "relay_seq": 5,
        "last_assistant_message": "current result",
    }
    with patch.object(CallProgress, "LULL", 0.1):
        task = asyncio.create_task(CallProgress(events).run(conversation))
        try:
            await asyncio.sleep(0)
            await hooks.ingest(payload, worker_only=True)
            await asyncio.sleep(0.01)
            conversation.send_text.assert_not_awaited()
            await hooks.ingest(
                {**payload, "relay_seq": 8, "hook_event_name": "PostToolUse"},
                worker_only=True,
            )
            await hooks.ingest(
                {
                    **payload,
                    "relay_seq": 6,
                    "last_assistant_message": "obsolete result",
                },
                worker_only=True,
            )
            await asyncio.sleep(0.12)
            conversation.send_context.assert_awaited_once()
            assert "current result" in conversation.send_context.call_args.args[0]
            conversation.send_text.assert_awaited_once()
            assert "event 5: finished" in conversation.send_text.call_args.args[0]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
