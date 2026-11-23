from __future__ import annotations

import asyncio
import json

from punt_vox.voxd.voice_call.boss_tools import BossTools

from .fakes import FakeWorkers, payload


def test_catalog_names_the_six_boss_tools() -> None:
    assert {tool.name for tool in BossTools.CATALOG} == {
        "start_worker",
        "steer_worker",
        "worker_status",
        "list_workers",
        "stop_worker",
        "remain_silent",
    }


async def test_identical_answers_to_two_decisions_both_reach_the_worker() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", pending_decision={"seq": 4})]
    tools = _tools(workers)
    answer: dict[str, object] = {
        "worker_id": "abcd1234",
        "words": "Yes, allow it.",
        "choice": "approve",
    }
    first = await tools.answer("steer_worker", answer, tool_call_id="first")
    assert not first[1]
    assert await tools.answer("steer_worker", answer, tool_call_id="first") == first
    workers.known = [payload("abcd1234", pending_decision={"seq": 9})]
    assert not (await tools.answer("steer_worker", answer, tool_call_id="second"))[1]
    assert workers.steered == [
        ("abcd1234", "Yes, allow it.", False),
        ("abcd1234", "Yes, allow it.", False),
    ]
    assert workers.choices == ["approve", "approve"]


async def test_ordinary_steer_reask_with_a_new_tool_id_replays_the_receipt() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234")]
    tools = _tools(workers)
    arguments: dict[str, object] = {"worker_id": "abcd1234", "words": "Continue."}
    first = await tools.answer("steer_worker", arguments, tool_call_id="interrupted")
    assert not first[1]
    assert await tools.answer("steer_worker", arguments, tool_call_id="reask") == first
    assert workers.steered == [("abcd1234", "Continue.", False)]
    assert tools.replayed == 1


async def test_explicit_denial_and_receipt_reach_the_worker_boundary() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", pending_decision={"seq": 4})]
    result, error = await _tools(workers).answer(
        "steer_worker",
        {"worker_id": "abcd1234", "words": "No, don't run that", "choice": "deny"},
        tool_call_id="denial",
    )
    assert not error
    assert json.loads(result)["choice"] == "deny"
    assert workers.choices == ["deny"]


async def test_invalid_choice_never_reaches_worker() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234")]
    result, error = await _tools(workers).answer(
        "steer_worker",
        {"worker_id": "abcd1234", "words": "maybe", "choice": "maybe"},
        tool_call_id="ambiguous",
    )
    assert error and "choice must be approve or deny" in result
    assert workers.steered == []


async def test_status_advertises_choices_only_with_a_pending_decision() -> None:
    workers = FakeWorkers()
    workers.known = [
        payload(
            "abcd1234",
            pending_decision={"seq": 4, "notification_type": "permission_prompt"},
        )
    ]
    tools = _tools(workers)
    result, error = await tools.answer(
        "worker_status", {"worker_id": "abcd1234"}, tool_call_id="pending"
    )
    assert not error
    assert json.loads(result)["decision_choices"] == ["approve", "deny"]
    workers.known = [payload("abcd1234")]
    result, error = await tools.answer(
        "worker_status", {"worker_id": "abcd1234"}, tool_call_id="clear"
    )
    assert not error
    assert json.loads(result)["decision_choices"] == []
    workers.known = [
        payload(
            "abcd1234", pending_decision={"seq": 5, "notification_type": "idle_prompt"}
        )
    ]
    result, error = await tools.answer(
        "worker_status", {"worker_id": "abcd1234"}, tool_call_id="idle"
    )
    assert not error
    assert json.loads(result)["decision_choices"] == []


def _tools(workers: FakeWorkers) -> BossTools:
    return BossTools(workers, workers)


async def test_start_defaults_to_the_only_allowlisted_repo() -> None:
    workers = FakeWorkers()
    result, error = await _tools(workers).answer(
        "start_worker", {"task": "fix x"}, tool_call_id="test-call"
    )
    assert not error
    assert json.loads(result)["state"] == "ready"
    assert workers.started == [("fix x", "/repo")]


async def test_start_without_project_needs_one_repo() -> None:
    tools = _tools(FakeWorkers(("/a", "/b")))
    result, error = await tools.answer(
        "start_worker", {"task": "fix x"}, tool_call_id="test-call"
    )
    assert error
    assert "/a" in json.loads(result)["error"]


async def test_reask_during_a_slow_start_starts_one_worker() -> None:
    workers = FakeWorkers()
    workers.gate.clear()
    tools = _tools(workers)
    first = asyncio.create_task(
        tools.answer("start_worker", {"task": "fix x"}, tool_call_id="test-call")
    )
    await asyncio.sleep(0)
    first.cancel()  # barge-in: the agent abandons the pending call
    reask = asyncio.create_task(
        tools.answer("start_worker", {"task": "fix x"}, tool_call_id="test-call")
    )
    await asyncio.sleep(0)
    workers.gate.set()
    assert (await reask)[1] is False
    assert (
        await tools.answer("start_worker", {"task": "fix x"}, tool_call_id="test-call")
        == await reask
    )
    assert workers.started == [("fix x", "/repo")]
    assert tools.replayed == 2


async def test_reask_spelling_out_defaults_replays_the_first_answer() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234")]
    tools = _tools(workers)
    first = await tools.answer(
        "start_worker", {"task": "fix x"}, tool_call_id="test-call"
    )
    spelled: dict[str, object] = {"task": "fix x", "project": "/repo"}
    assert (
        await tools.answer("start_worker", spelled, tool_call_id="test-call") == first
    )
    steer: dict[str, object] = {"worker_id": "abcd", "words": "go"}
    await tools.answer("steer_worker", steer, tool_call_id="test-call")
    await tools.answer(
        "steer_worker", {**steer, "urgent": False}, tool_call_id="test-call"
    )
    assert workers.started == [("fix x", "/repo")]
    assert workers.steered == [("abcd1234", "go", False)]


async def test_steer_resolves_a_spoken_prefix_and_passes_words_verbatim() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234"), payload("ef567890")]
    result, error = await _tools(workers).answer(
        "steer_worker",
        {"worker_id": "abcd", "words": "use tabs;", "urgent": True},
        tool_call_id="test-call",
    )
    assert not error
    assert json.loads(result)["heard"] is True
    assert workers.steered == [("abcd1234", "use tabs;", True)]


async def test_ambiguous_or_short_ids_are_refused() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234"), payload("abcd9999")]
    tools = _tools(workers)
    for wanted in ("abcd", "a"):
        _, error = await tools.answer(
            "stop_worker", {"worker_id": wanted}, tool_call_id="test-call"
        )
        assert error
    assert workers.stopped == []


async def test_status_reports_curated_progress_only() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", goal="ship it", gaps={"x": 1})]
    result, _ = await _tools(workers).answer(
        "worker_status", {"worker_id": "abcd1234"}, tool_call_id="test-call"
    )
    assert json.loads(result)["progress"] == {"goal": "ship it"}


async def test_list_stop_and_silence() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234")]
    tools = _tools(workers)
    listed, _ = await tools.answer("list_workers", {}, tool_call_id="test-call")
    assert [w["worker_id"] for w in json.loads(listed)] == ["abcd1234"]
    stopped, _ = await tools.answer(
        "stop_worker", {"worker_id": "abcd1234"}, tool_call_id="test-call"
    )
    assert json.loads(stopped)["state"] == "stopped"
    assert await tools.answer("remain_silent", {}, tool_call_id="test-call") == (
        '{"silent": true}',
        False,
    )


async def test_unknown_tool_and_bad_arguments_are_errors() -> None:
    tools = _tools(FakeWorkers())
    assert (await tools.answer("rm_rf", {}, tool_call_id="test-call"))[1]
    assert (
        await tools.answer("start_worker", {"task": " "}, tool_call_id="test-call")
    )[1]
    assert (
        await tools.answer(
            "steer_worker", {"worker_id": "x", "urgent": 1}, tool_call_id="test-call"
        )
    )[1]


async def test_bad_repository_settings_fail_the_tool_not_the_call() -> None:
    class Unreadable(FakeWorkers):
        def repos(self) -> tuple[str, ...]:
            raise ValueError("worker_repos is not a list")

    workers = Unreadable()
    result, error = await _tools(workers).answer(
        "start_worker", {"task": "fix x"}, tool_call_id="test-call"
    )
    assert error
    assert "not a list" in json.loads(result)["error"]


async def test_start_refuses_a_project_that_is_not_a_path() -> None:
    result, error = await _tools(FakeWorkers()).answer(
        "start_worker", {"task": "fix x", "project": 3}, tool_call_id="test-call"
    )
    assert error
    assert "string" in json.loads(result)["error"]
