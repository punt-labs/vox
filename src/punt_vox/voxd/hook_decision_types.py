"""Shared projection of an outstanding harness decision."""

from __future__ import annotations

from typing import TypedDict


class PendingDecision(TypedDict):
    """A harness question still awaiting input."""

    notification_type: str
    message: str
    seq: int
