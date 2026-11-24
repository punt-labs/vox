"""Thin worker control RPC client using voxd's authenticated transport."""

from __future__ import annotations

from typing import cast, final

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.daemon_control import DaemonControl


@final
class WorkerClient:
    """Make short-lived worker requests without local lifecycle logic."""

    __slots__ = ()

    @staticmethod
    async def request(operation: str, **fields: str | bool | int) -> object:
        """Return a registry result or surface the daemon's refusal."""
        return await DaemonControl("worker").request(
            operation, timeout=90 if operation == "start" else 30, **fields
        )

    @staticmethod
    def paths(reply: object) -> list[str]:
        """Return a ``repos``-family reply as a list of paths.

        Raises:
            VoxdProtocolError: If the daemon answered anything but a list.
        """
        if not isinstance(reply, list):
            raise VoxdProtocolError("expected a list of paths")
        return [str(p) for p in cast("list[object]", reply)]
