"""Named properties from docs/vox-workers.tex."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, is_dataclass, replace
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.hook_decisions import HookDecisions
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.codec import WorkerRecordCodec
from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerInput, WorkerProvider
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.steer_delivery import SteerDelivery
from punt_vox.voxd.workers.steering_types import DecisionChoice


def test_worker_record_is_a_frozen_domain_value(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    assert is_dataclass(record)
    with pytest.raises(FrozenInstanceError):
        object.__getattribute__(record, "__setattr__")("state", "stopped")
    with pytest.raises(TypeError):
        object.__getattribute__(record, "last_report")["session_started"] = True


def test_worker_launch_obeys_parameter_limit() -> None:
    parameters = inspect.signature(WorkerProvider.start).parameters
    assert len(parameters) - 1 <= 4


def test_worker_record_detaches_report_snapshots_on_both_edges(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    files = ["a.py"]
    record = replace(record, last_report={"active_files": files})
    files.append("outside.py")
    wire = WorkerRecordCodec.encode(record)
    cast("list[str]", wire["last_report"]["active_files"]).append("wire.py")
    assert record.last_report == {"active_files": ["a.py"]}


@pytest.mark.parametrize("kind", ["permission_prompt", "idle_prompt"])
def test_decision_notification_invariant_newest_question_wins(kind: str) -> None:
    """DecisionNotification retains only the newest admitted question."""
    state = HookDecisions()
    state.ingest(3, "Notification", {"notification_type": kind, "message": "new"})
    state.ingest(2, "Notification", {"notification_type": kind, "message": "old"})
    assert state.pending() == {"seq": 3, "notification_type": kind, "message": "new"}


def test_decision_hook_receipt_invariant_bounded_ordered_permission_progress() -> None:
    """DecisionHookReceipt keeps at most 64 strictly newer permission receipts."""
    state = HookDecisions()
    state.ingest(3, "Notification", {"notification_type": "permission_prompt"})
    state.ingest(2, "PostToolUse", {})
    assert state.receipts(3) == []
    for seq in range(4, 74):
        state.ingest(seq, "Notification", {"notification_type": "other"})
    assert state.receipts(3) == list(range(10, 74))
    assert state.pending() == {
        "seq": 3,
        "notification_type": "permission_prompt",
        "message": "",
    }


def test_decision_progress_invariant_old_hooks_cannot_clear_or_resurrect() -> None:
    """DecisionProgress clears a question only on newer non-Notification input."""
    state = HookDecisions()
    state.ingest(3, "Notification", {"notification_type": "permission_prompt"})
    state.ingest(2, "Stop", {})
    assert state.pending() is not None
    state.ingest(4, "PostToolUse", {})
    assert state.pending() is None
    state.ingest(3, "Notification", {"notification_type": "permission_prompt"})
    assert state.pending() is None


def test_decision_receipt_heard_invariant_requires_question_at_delivery() -> None:
    """DecisionReceiptHeard selects the earliest hook after an existing question."""
    store = HookContextStore()
    profile = ClaudeProfile(store)
    payload: dict[str, object] = {
        "session_id": "worker",
        "relay_seq": 3,
        "hook_event_name": "Notification",
        "notification_type": "permission_prompt",
    }
    store.ingest(payload, worker_input=True)
    for seq in (4, 5):
        store.ingest(
            {**payload, "relay_seq": seq, "hook_event_name": "PostToolUse"},
            worker_input=True,
        )
    assert profile.inputs_since("worker", 2) == []
    assert profile.inputs_since("worker", 3) == [WorkerInput(4, "", decision=True)]
    assert profile.inputs_since("worker", 4) == []
    assert store.last_input("worker") == (0, "")


@pytest.mark.parametrize("choice", ["approve", "deny"])
def test_decision_delivery_invariant_permission_and_explicit_choice(
    choice: DecisionChoice,
) -> None:
    """DecisionDelivery sends a choice only when a permission dialog is pending."""
    profile = MagicMock(spec=HarnessProfile)
    provider = MagicMock(spec=WorkerProvider)
    record = MagicMock(spec=WorkerRecord)
    profile.last_input.return_value = WorkerInput(3, "", decision=True)
    delivery = SteerDelivery(profile, provider)
    assert delivery.send(record, "answer", urgent=False, choice=choice) == 3
    provider.decide.assert_called_once_with(record.session, choice, profile=profile)
    provider.steer.assert_not_called()
    provider.reset_mock()
    profile.last_input.return_value = WorkerInput(1, "task")
    with pytest.raises(ValueError, match="choice requires"):
        delivery.send(record, "answer", urgent=False, choice=choice)
    provider.decide.assert_not_called()
    provider.steer.assert_not_called()
