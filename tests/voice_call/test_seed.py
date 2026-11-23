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
