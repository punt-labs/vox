from __future__ import annotations

import json
from typing import Any

from punt_vox.voxd.voice_call.seed import CallSeed

from .fakes import payload


def _state(prompt: str) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(prompt.rsplit("(JSON): ", 1)[1])
    return state


def test_seed_lists_projects_and_workers_newest_first() -> None:
    prompt = CallSeed(("/repo",), [payload("a"), payload("bbb")]).prompt()
    assert '["/repo"]' in prompt
    assert "start_worker uses that repository" in prompt
    workers = _state(prompt)["workers"]
    assert [w["worker_id"] for w in workers] == ["bbb", "a"]


def test_seed_maps_explicit_answers_and_reasks_ambiguous_ones() -> None:
    prompt = CallSeed(("/repo",), []).prompt()
    assert "choice=approve for an explicit yes" in prompt
    assert "choice=deny for an explicit no" in prompt
    assert "ambiguous, ask yes or no again instead of steering" in prompt
    assert "never set urgent for a decision" in prompt
    assert "copy the seq of the exact question you asked" in prompt
    assert "never replace it with a newer question's seq" in prompt


def test_seed_drops_oldest_workers_to_stay_under_10_kb() -> None:
    workers = [
        payload(f"w{i:03d}" + "x" * i, "t" * 380, goal="g" * 380) for i in range(40)
    ]
    prompt = CallSeed(("/a", "/b"), workers).prompt()
    assert len(prompt.encode()) <= CallSeed.LIMIT
    state = _state(prompt)
    kept = state["workers"]
    assert isinstance(kept, list)
    assert 0 < len(kept) < 40
    assert state["older_workers_omitted"] == 40 - len(kept)
    assert kept[0]["worker_id"].startswith("w039")


def test_seed_with_no_repositories_says_so_once_and_offers_no_workers() -> None:
    prompt = CallSeed((), []).prompt()
    assert "No repository is allowlisted" in prompt
    assert "vox worker allow" in prompt
    assert "say that once" in prompt
    assert "do not offer to start workers" in prompt
    assert "start_worker uses that repository" not in prompt


def test_seed_with_repositories_carries_no_empty_list_instruction() -> None:
    prompt = CallSeed(("/repo",), []).prompt()
    assert "No repository is allowlisted" not in prompt
