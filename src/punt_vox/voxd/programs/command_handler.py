"""The shared base for the mutating program wire handlers (template method).

Every mutating ``program_*`` handler is the same thin adapter: parse the wire
message, hand the work to the :class:`ProgramService` (which POSTs one serialized
:class:`ControlSignal` -- the handler never touches the Program), and reply. Only
the parse-and-dispatch step differs per command, so it is the one abstract hook;
the request-id plumbing, the applied ack, and the boundary error reply live here
once (DRY, replacing the copy-pasted try/except of the old music handlers).

The boundary catches every *expected* domain failure and turns it into a wire
``{"type": "error"}`` a client can read: a ``ValueError`` (a bad request or a
lost-race guard), a ``LookupError`` (``store.open`` on a deleted album dir), and
an ``OSError`` (``store.create``'s ``mkdir(exist_ok=False)`` mint-race guard,
disk-full, permissions). Letting any of these escape would tear the socket down,
leaving the client a generic "connection closed" instead of the cause. Handlers
hold no session and no owner -- ``voxd`` is machine-universal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["ProgramCommandHandler"]


class ProgramCommandHandler(ABC):
    """A mutating ``program_*`` handler: parse, dispatch to the service, ack."""

    __slots__ = ("_service",)
    _service: ProgramService
    _WIRE_TYPE: ClassVar[str]
    """The reply ``type`` (and inbound message type) this handler answers."""

    def __new__(cls, service: ProgramService) -> Self:
        self = super().__new__(cls)
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Parse and dispatch, replying with an ack or a boundary error."""
        request_id = str(msg.get("id", ""))
        try:
            self._run(msg)
        except (ValueError, LookupError, OSError) as exc:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": str(exc)}
            )
            return
        await websocket.send_json({"type": self._WIRE_TYPE, "id": request_id})

    @abstractmethod
    def _run(self, msg: dict[str, object], /) -> None:
        """Parse ``msg`` and issue the one serialized command to the service."""

    @staticmethod
    def _opt_str(msg: dict[str, object], key: str) -> str | None:
        """Return a present string field, or ``None`` when absent (the contract)."""
        value = msg.get(key)
        return value if isinstance(value, str) else None
