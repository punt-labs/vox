"""The call ID a wire request names, or asks the daemon to choose."""

from __future__ import annotations

import uuid
from typing import final

__all__ = ["CallId"]


@final
class CallId:
    """Read a request's ``call_id`` without treating a malformed one as absent."""

    __slots__ = ()

    @staticmethod
    def requested(msg: dict[str, object]) -> uuid.UUID | None:
        """Return the named ID; None when the request leaves the choice open.

        Raises:
            ValueError: When a scoped request names no ID, or an ID is not a UUID.
        """
        if "call_id" not in msg:
            if msg.get("type") in ("call_start_scoped", "call_stop_scoped"):
                raise ValueError("call_id is required for a scoped request")
            return None
        value = msg["call_id"]
        if not isinstance(value, str):
            raise ValueError("call_id must be a UUID string")
        try:
            return uuid.UUID(value)
        except ValueError:
            raise ValueError("call_id must be a UUID string") from None

    @classmethod
    def for_start(cls, msg: dict[str, object]) -> uuid.UUID:
        """Return the ID a call_start names, or a fresh one."""
        return cls.requested(msg) or uuid.uuid4()
