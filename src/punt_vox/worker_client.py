"""Thin worker control RPC client using voxd's authenticated transport."""

from __future__ import annotations

from typing import final

from punt_vox.daemon_control import DaemonControl


@final
class WorkerClient:
    """Make short-lived worker requests without local lifecycle logic."""

    __slots__ = ()

    @staticmethod
    async def request(operation: str, **fields: str | bool) -> object:
        """Return a registry result or surface the daemon's refusal."""
        return await DaemonControl("worker").request(
            operation, timeout=90 if operation == "start" else 30, **fields
        )
