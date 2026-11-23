"""The pending-decision snapshot is a complete JSON-compatible contract."""

from __future__ import annotations

import json

from punt_vox.voxd.hook_decision_types import PendingDecision


def test_pending_decision_requires_type_message_and_sequence() -> None:
    decision = PendingDecision(notification_type="permission_prompt", message="", seq=3)
    assert set(decision) == PendingDecision.__required_keys__
    assert not PendingDecision.__optional_keys__
    assert json.loads(json.dumps(decision)) == decision
