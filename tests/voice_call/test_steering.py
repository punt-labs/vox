"""Steering identity follows the question asked, not the newest notification."""

from __future__ import annotations

import pytest

from punt_vox.voxd.voice_call.steering import SteeringTool
from punt_vox.voxd.voice_call.worker_directory import WorkerDirectory

from .fakes import FakeWorkers, payload


async def test_choice_resolution_keeps_the_announced_question() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", prompt_epoch=9)]
    resolved = await SteeringTool(workers, WorkerDirectory(workers)).resolve(
        {"worker_id": "abcd", "words": "Yes!", "choice": "approve", "prompt_seq": 4}
    )
    assert resolved["worker_id"] == "abcd1234"
    assert resolved["prompt_seq"] == resolved["prompt_epoch"] == 4


@pytest.mark.parametrize("seq", [None, 0, -1, True, False, "4"])
async def test_choice_requires_a_positive_prompt_sequence(seq: object) -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", prompt_epoch=9)]
    with pytest.raises(ValueError, match="choice requires prompt_seq"):
        await SteeringTool(workers, WorkerDirectory(workers)).resolve(
            {
                "worker_id": "abcd1234",
                "words": "Yes",
                "choice": "approve",
                "prompt_seq": seq,
            }
        )
    assert workers.steered == []


async def test_empty_answer_is_rejected_before_caching() -> None:
    workers = FakeWorkers()
    with pytest.raises(ValueError, match="words must be a non-empty string"):
        await SteeringTool(workers, WorkerDirectory(workers)).resolve(
            {"worker_id": "abcd", "words": " "}
        )


@pytest.mark.parametrize("seq", [None, 4, True, 0, "private spoken answer", {}])
@pytest.mark.parametrize("explicit_none", [False, True])
async def test_prompt_sequence_requires_a_choice(
    seq: object, explicit_none: bool
) -> None:
    workers = FakeWorkers()
    arguments: dict[str, object] = {
        "worker_id": "abcd",
        "words": "Continue.",
        "prompt_seq": seq,
    }
    if explicit_none:
        arguments["choice"] = None
    with pytest.raises(ValueError, match="prompt_seq requires choice"):
        await SteeringTool(workers, WorkerDirectory(workers)).resolve(arguments)
    assert workers.steered == []


async def test_ordinary_text_keeps_exact_words_and_current_epoch() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", prompt_epoch=9)]
    tool = SteeringTool(workers, WorkerDirectory(workers))
    first = await tool.resolve({"worker_id": "abcd", "words": "Set X"})
    second = await tool.resolve({"worker_id": "abcd", "words": "set X"})
    assert first["prompt_epoch"] == 9
    assert tool.identity(first) != tool.identity(second)


@pytest.mark.parametrize(
    "arguments,error",
    [
        ({"urgent": "yes"}, "urgent must be a boolean"),
        ({"choice": "maybe"}, "choice must be approve or deny"),
        (
            {"choice": "approve", "urgent": True, "prompt_seq": 4},
            "must not interrupt the permission dialog",
        ),
    ],
)
async def test_invalid_steering_modes_are_rejected_before_delivery(
    arguments: dict[str, object], error: str
) -> None:
    workers = FakeWorkers()
    with pytest.raises(ValueError, match=error):
        await SteeringTool(workers, WorkerDirectory(workers)).resolve(
            {"worker_id": "abcd", "words": "Yes", **arguments}
        )
