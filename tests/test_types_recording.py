"""Tests for the recording request wire schema."""

from __future__ import annotations

from typing import Literal, get_type_hints

from punt_vox.types_recording import RecordingFetchRequest


def test_fetch_request_requires_the_daemon_wire_fields() -> None:
    assert RecordingFetchRequest.__required_keys__ == {"type", "id", "ref"}
    assert get_type_hints(RecordingFetchRequest) == {
        "type": Literal["fetch"],
        "id": str,
        "ref": str,
    }
