"""Lightweight steering wire types, independent of worker implementations."""

from __future__ import annotations

from typing import TypedDict


class SteerReceipt(TypedDict):
    """Describe observed input, never a claim that a turn ended."""

    worker_id: str
    heard: bool
    receipt_seq: int | None  # PY-TS-14: no sequence exists without a heard receipt.
    urgent: bool
