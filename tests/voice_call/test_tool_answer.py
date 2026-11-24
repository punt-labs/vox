"""Tests for punt_vox.voxd.voice_call.tool_answer -- outcome to spoken JSON."""

from __future__ import annotations

import errno
import json
import logging
from typing import TYPE_CHECKING

from punt_vox.voxd.voice_call.tool_answer import ToolAnswer

if TYPE_CHECKING:
    import pytest


async def _request() -> tuple[dict[str, object], dict[str, object]]:
    arguments: dict[str, object] = {"task": "fix x"}
    return arguments, arguments


async def test_result_is_json_and_not_an_error() -> None:
    async def operation(arguments: dict[str, object]) -> object:
        return {"started": arguments["task"]}

    result, error = await ToolAnswer().run(
        "start_worker", _request, operation, durable=True
    )
    assert not error
    assert json.loads(result) == {"started": "fix x"}


async def test_refusal_is_spoken_verbatim() -> None:
    async def operation(_arguments: dict[str, object]) -> object:
        raise ValueError("name a project")

    result, error = await ToolAnswer().run(
        "start_worker", _request, operation, durable=True
    )
    assert error
    assert json.loads(result)["error"] == "name a project"


async def test_config_fault_is_spoken_without_the_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def operation(_arguments: dict[str, object]) -> object:
        raise OSError(errno.EBADMSG, "no closing ---", "/home/someone/vox.md")

    with caplog.at_level(logging.ERROR):
        result, error = await ToolAnswer().run(
            "start_worker", _request, operation, durable=True
        )
    assert error
    assert json.loads(result)["error"] == "operation failed"
    logged = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("/home/someone/vox.md" in line for line in logged)


async def test_fault_while_resolving_the_request_is_classified_too(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def request() -> tuple[dict[str, object], dict[str, object]]:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    async def operation(_arguments: dict[str, object]) -> object:
        raise AssertionError("never reached")

    with caplog.at_level(logging.ERROR):
        result, error = await ToolAnswer().run(
            "start_worker", request, operation, durable=True
        )
    assert error
    assert json.loads(result)["error"] == "operation failed"


async def test_durable_request_replays_the_first_answer() -> None:
    runs: list[int] = []

    async def operation(_arguments: dict[str, object]) -> object:
        runs.append(1)
        return {"ok": True}

    answer = ToolAnswer()
    first = await answer.run("start_worker", _request, operation, durable=True)
    assert await answer.run("start_worker", _request, operation, durable=True) == first
    assert runs == [1]
    assert answer.replayed == 1
