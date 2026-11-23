from __future__ import annotations

import asyncio

import pytest

from punt_vox.voxd.voice_call.result_cache import ToolResultCache


def test_key_ignores_argument_order() -> None:
    assert ToolResultCache.key("t", {"a": 1, "b": 2}) == ToolResultCache.key(
        "t", {"b": 2, "a": 1}
    )


async def test_durable_result_replays_without_executing_again() -> None:
    cache, runs = ToolResultCache(), []

    async def execute() -> str:
        runs.append(1)
        return "done"

    assert await cache.run("k", execute, durable=True) == "done"
    assert await cache.run("k", execute, durable=True) == "done"
    assert runs == [1]
    assert cache.hits == 1


async def test_reads_share_only_the_execution_in_flight() -> None:
    cache, gate, runs = ToolResultCache(), asyncio.Event(), []

    async def execute() -> str:
        runs.append(1)
        await gate.wait()
        return f"run {len(runs)}"

    first = asyncio.create_task(cache.run("k", execute, durable=False))
    second = asyncio.create_task(cache.run("k", execute, durable=False))
    await asyncio.sleep(0)
    gate.set()
    assert list(await asyncio.gather(first, second)) == ["run 1", "run 1"]
    assert await cache.run("k", execute, durable=False) == "run 2"


async def test_cancelled_asker_does_not_cancel_the_execution() -> None:
    cache, gate = ToolResultCache(), asyncio.Event()

    async def execute() -> str:
        await gate.wait()
        return "started"

    abandoned = asyncio.create_task(cache.run("k", execute, durable=True))
    await asyncio.sleep(0)
    abandoned.cancel()
    reask = asyncio.create_task(cache.run("k", execute, durable=True))
    await asyncio.sleep(0)
    gate.set()
    assert await reask == "started"
    with pytest.raises(asyncio.CancelledError):
        await abandoned


async def test_failure_is_not_cached() -> None:
    cache, runs = ToolResultCache(), []

    async def execute() -> str:
        runs.append(1)
        if len(runs) == 1:
            raise ValueError("busy")
        return "ok"

    with pytest.raises(ValueError, match="busy"):
        await cache.run("k", execute, durable=True)
    assert await cache.run("k", execute, durable=True) == "ok"


def test_key_ignores_case_and_punctuation_of_a_reask() -> None:
    first = ToolResultCache.key("start_worker", {"task": "Print 'Hello, world!'"})
    again = ToolResultCache.key("start_worker", {"task": "print hello world."})
    other = ToolResultCache.key("start_worker", {"task": "print goodbye world"})
    assert first == again != other


def test_key_keeps_paths_and_ids_exact() -> None:
    dashed = ToolResultCache.key("start_worker", {"task": "t", "project": "/a/b-c"})
    nested = ToolResultCache.key("start_worker", {"task": "t", "project": "/a/b/c"})
    assert dashed != nested


def test_key_keeps_signs_that_change_the_instruction() -> None:
    negative = ToolResultCache.key("steer_worker", {"words": "set it to -5."})
    positive = ToolResultCache.key("steer_worker", {"words": "Set it to 5"})
    assert negative != positive


@pytest.mark.parametrize("words", ["set X", "Set x", "Set X!", "Set  X"])
def test_key_preserves_exact_steering_words(words: str) -> None:
    assert ToolResultCache.key(
        "steer_worker", {"words": "Set X"}
    ) != ToolResultCache.key("steer_worker", {"words": words})


def test_empty_steering_words_remain_distinct_from_missing_words() -> None:
    assert ToolResultCache.key("steer_worker", {"words": ""}) != ToolResultCache.key(
        "steer_worker", {}
    )
