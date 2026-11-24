"""The shared base for the mutating program wire handlers (template method).

Every mutating ``program_*`` handler parses the wire message, hands the work to
:class:`ProgramService`, and replies. Only parse-and-dispatch varies by command.
An unmet synchronous playback guard is a no-op, not a malformed request.

The boundary catches every *expected* domain failure and replies through
:class:`WireReply`, which splits it by fault-vs-error: a ``ValueError`` for a bad
request is audited as ``error``,
while a ``LookupError`` (``store.open`` on a deleted album dir) or an ``OSError``
(``store.create``'s ``mkdir(exist_ok=False)`` mint-race guard, disk-full,
permissions) is a server-side operational failure audited as ``fault``. Letting
any escape would tear the socket down, leaving the client a generic "connection
closed" instead of the cause; replying through :class:`WireReply` also id-stamps
every frame and no-ops on a gone peer. Handlers hold no session and no owner --
``voxd`` is machine-universal.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Self

from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.types_programs.control import CommandOutcome
    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["ProgramCommandHandler"]

logger = logging.getLogger(__name__)


class ProgramCommandHandler(ABC):
    """A mutating ``program_*`` handler: parse, dispatch to the service, ack."""

    __slots__ = ("_service",)
    _service: ProgramService
    _WIRE_TYPE: ClassVar[str]
    """The reply ``type`` (and inbound message type) this handler answers."""

    def __new__(cls, service: ProgramService) -> Self:
        """Create a program command handler."""
        self = super().__new__(cls)
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Parse and dispatch, replying with an ack, a rejection, or a fault.

        Unmet playback guards return a command reply with ``applied=False``.
        :meth:`WireReply.reject_or_fault` handles other expected failures.
        """
        reply = WireReply(websocket, str(msg.get("id", "")))
        payload: dict[str, object] = {"type": self._WIRE_TYPE}
        try:
            outcome = self._run(msg)
        except (ValueError, LookupError, OSError) as exc:
            await reply.reject_or_fault(exc)
            return
        if outcome is not None:
            logger.info("%s skipped: reason=not_applied", self._WIRE_TYPE)
            payload.update(applied=outcome.applied, message=outcome.message)
        await reply.send(payload)

    @abstractmethod
    def _run(self, msg: dict[str, object], /) -> CommandOutcome | None:
        """Issue the command, returning a synchronous refusal if not applied."""

    @staticmethod
    def _opt_str(msg: dict[str, object], key: str) -> str | None:
        """Return a present string field, or ``None`` when absent or null.

        Absence (missing key or ``null``) is the documented "not supplied"
        contract. A present-but-wrong-typed value (e.g. a JSON number) is a
        malformed request, not an absent field: it raises so the boundary answers
        with a wire error rather than silently ignoring it and falling through to
        a different resolution path (a numeric ``album_id`` must not become a
        catch-all tag query).
        """
        value = msg.get(key)
        if value is None or isinstance(value, str):
            return value
        msg_text = f"field {key!r} must be a string, got {type(value).__name__}"
        raise ValueError(msg_text)
