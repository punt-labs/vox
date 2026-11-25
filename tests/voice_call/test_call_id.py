"""Tests for :mod:`punt_vox.voxd.voice_call.call_id`."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from punt_vox.voxd.voice_call.call_id import CallId


def test_a_named_id_is_returned() -> None:
    call_id = uuid4()
    assert CallId.requested({"type": "call_stop", "call_id": str(call_id)}) == call_id


def test_an_unscoped_request_may_leave_the_id_open() -> None:
    assert CallId.requested({"type": "call_stop"}) is None
    assert isinstance(CallId.for_start({"type": "call_start"}), UUID)


@pytest.mark.parametrize("kind", ["call_start_scoped", "call_stop_scoped"])
def test_a_scoped_request_must_name_an_id(kind: str) -> None:
    with pytest.raises(ValueError, match="call_id is required for a scoped request"):
        CallId.requested({"type": kind})


@pytest.mark.parametrize("value", [42, "not-a-uuid", None])
def test_a_malformed_id_is_refused_not_ignored(value: object) -> None:
    with pytest.raises(ValueError, match="call_id must be a UUID string"):
        CallId.requested({"type": "call_stop", "call_id": value})
