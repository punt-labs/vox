"""Lightweight contracts for daemon-owned hook ingestion."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol

__all__ = ["HookIngest"]


class HookIngest(Protocol):
    """Accept a live JSON envelope for validation, redaction and checkpointing."""

    # PY-TS-14: this JSON wire input is validated and redacted by the store.
    def __call__(
        self, payload: dict[str, object], *, worker_only: bool
    ) -> Awaitable[None]:
        """Ingest an envelope before returning a delivery receipt."""
        ...
