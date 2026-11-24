"""Tests for :mod:`punt_vox.voxd.voice_call.call_trace`."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from punt_vox.voxd.voice_call.call_trace import CallTrace

if TYPE_CHECKING:
    from pathlib import Path


def test_reserve_creates_a_private_file_named_by_the_call_id(tmp_path: Path) -> None:
    call_id = uuid4()
    trace = CallTrace.reserve(tmp_path, call_id)
    try:
        assert trace.path == tmp_path / f"call-{call_id.hex}.jsonl"
        assert trace.path.stat().st_mode & 0o777 == 0o600
        trace.events.record("in", "ping", {"event_id": 1})
    finally:
        trace.stream.close()
    assert '"ping"' in trace.path.read_text()


def test_a_reused_call_id_is_refused(tmp_path: Path) -> None:
    call_id = uuid4()
    CallTrace.reserve(tmp_path, call_id).stream.close()
    with pytest.raises(ValueError, match="call_id has already been used"):
        CallTrace.reserve(tmp_path, call_id)
