"""Durable hook ordering validation."""

import pytest

from punt_vox.voxd.hook_session_context import SessionContext
from punt_vox.voxd.hook_snapshot import CheckpointOrdering


def test_checkpoint_rejects_result_beyond_observed_sequences() -> None:
    checkpoint = SessionContext().checkpoint()
    CheckpointOrdering.validate(checkpoint)
    checkpoint["result_seq"] = checkpoint["gaps"]["highest_seen"] + 1
    with pytest.raises(ValueError, match="Invalid worker hook checkpoint ordering"):
        CheckpointOrdering.validate(checkpoint)
