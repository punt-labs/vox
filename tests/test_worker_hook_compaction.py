"""Oversized hook projection retains the managed worker's admission identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO, cast
from unittest.mock import patch

import pytest
from test_workers import FakeWorkerProvider, _registry

from punt_vox.hook_context_relay import HookContextRelay


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "PostToolUse"])
def test_compaction_preserves_managed_worker_admission(
    tmp_path: Path, event: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    worker = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    captured: list[dict[str, object]] = []

    def capture(_args: list[str], **kwargs: object) -> None:
        source = cast("TextIO", kwargs["stdin"])
        captured.append(json.load(source))

    monkeypatch.setattr("punt_vox.hook_context_relay.user_state_dir", lambda: tmp_path)
    with patch("punt_vox.hook_context_relay.subprocess.Popen", side_effect=capture):
        HookContextRelay(
            {
                "session_id": worker.id,
                "cwd": worker.worktree,
                "hook_event_name": event,
                "extra": "x" * 300_000,
            }
        ).submit(worker_only=True)
    staged = captured[0]
    assert len(json.dumps(staged)) <= 240 * 1024
    assert registry.admits_hook(staged["session_id"], staged.get("cwd"))
