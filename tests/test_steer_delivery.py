"""Safe delivery distinguishes permission choices from literal task input."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerInput, WorkerProvider
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.steer_delivery import SteerDelivery
from punt_vox.voxd.workers.steering_types import DecisionChoice


@pytest.mark.parametrize("choice", ["approve", "deny"])
def test_explicit_choice_never_uses_literal_input(choice: DecisionChoice) -> None:
    profile = MagicMock(spec=HarnessProfile)
    profile.last_input.return_value = WorkerInput(3, "", decision=True)
    provider = MagicMock(spec=WorkerProvider)
    record = MagicMock(spec=WorkerRecord, id="worker", session="session")
    assert (
        SteerDelivery(profile, provider).send(
            record, "person's answer", urgent=False, choice=choice
        )
        == 3
    )
    provider.decide.assert_called_once_with("session", choice, profile=profile)
    provider.steer.assert_not_called()


def test_empty_input_history_uses_ordinary_delivery_and_zero_baseline() -> None:
    profile = MagicMock(spec=HarnessProfile)
    profile.last_input.return_value = None
    provider = MagicMock(spec=WorkerProvider)
    record = MagicMock(spec=WorkerRecord, id="worker", session="session")
    assert (
        SteerDelivery(profile, provider).send(record, "words", urgent=True, choice=None)
        == 0
    )
    provider.steer.assert_called_once_with(
        "session", "words", urgent=True, profile=profile
    )
    provider.decide.assert_not_called()


def test_delivery_failure_does_not_return_a_receipt_baseline() -> None:
    profile = MagicMock(spec=HarnessProfile)
    profile.last_input.return_value = WorkerInput(3, "", decision=True)
    provider = MagicMock(spec=WorkerProvider)
    provider.decide.side_effect = OSError("closed")
    with pytest.raises(OSError, match="closed"):
        SteerDelivery(profile, provider).send(
            MagicMock(spec=WorkerRecord), "no", urgent=False, choice="deny"
        )
