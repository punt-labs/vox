"""Worker milestones are immutable values independent of the voice provider."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict

import pytest

from punt_vox.voxd.voice_call.progress_types import WorkerProgress


def test_finished_progress_has_no_notification_and_allows_empty_text() -> None:
    event = WorkerProgress("worker", 3, "finished", "")
    assert asdict(event) == {
        "worker_id": "worker",
        "seq": 3,
        "kind": "finished",
        "text": "",
        "notification_type": "",
    }


@pytest.mark.parametrize("attribute", ["seq", "text"])
def test_decision_progress_retains_identity_and_cannot_be_mutated(
    attribute: str,
) -> None:
    event = WorkerProgress("worker", 4, "needs_decision", "Allow?", "permission_prompt")
    assert event == WorkerProgress(
        "worker", 4, "needs_decision", "Allow?", "permission_prompt"
    )
    with pytest.raises(FrozenInstanceError):
        setattr(event, attribute, 5)
