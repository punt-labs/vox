"""Wire types for daemon recording requests."""

from __future__ import annotations

from typing import Literal, TypedDict


class RecordingFetchRequest(TypedDict):
    """Request for one recording's bytes."""

    type: Literal["fetch"]
    id: str
    ref: str
