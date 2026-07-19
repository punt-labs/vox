"""Receive a shipped client log frame and re-emit it into the daemon's ``vox.log``.

The single-writer daemon owns the one durable log; every client ships its records
here over the WebSocket. This handler reconstructs each frame as a
:class:`logging.LogRecord` and hands it to one fixed ``client`` logger, so a
client line lands in ``vox.log`` beside the daemon's own -- one file, one writer,
no multi-process rotation race.

Three safety rules hold at this authenticated write boundary. The record is
emitted with ``args=None`` so an untrusted value can never be re-interpolated
into a second record. It always uses the *fixed* ``client`` logger name -- never
``getLogger(<untrusted-field>)`` -- so a client streaming unique names cannot grow
the logger cache without bound; the origin (``role.name``) rides in the message
instead. And the daemon file handler's :class:`SanitizingFormatter` escapes the
*final* formatted line, so a newline or control byte in any field (the origin or
the message) stays one physical line. A frame that fails to parse is refused with
a metadata-only WARNING -- the field and type that were wrong, never the payload.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.log_wire import LogRecordWire
from punt_vox.voxd.types import MessageHandler

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

__all__ = ["LogHandler"]

logger = logging.getLogger(__name__)

_LEVELS = logging.getLevelNamesMapping()
_CLIENT_LOGGER = "client"


@final
class LogHandler(MessageHandler):
    """Handle ``log`` frames: re-emit each shipped record into ``vox.log``."""

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Re-emit one shipped record; a malformed frame is refused, never written.

        Send-only: the client awaits no ack, so this reads the frame and returns
        without touching *websocket* -- keeping log shipping off the RPC round-trip.
        """
        _ = websocket
        try:
            wire = LogRecordWire.from_wire(msg)
        except ValueError as exc:
            # Metadata only -- the offending field and type, never the payload.
            logger.warning("rejected malformed log frame: %s", exc)
            return
        logging.getLogger(_CLIENT_LOGGER).handle(self._reconstruct(wire))

    @staticmethod
    def _reconstruct(wire: LogRecordWire) -> logging.LogRecord:
        """Build a record on the fixed ``client`` logger, origin folded into the msg.

        ``args=None`` means ``getMessage`` returns the message verbatim (no second
        interpolation). The origin (``role.name``) rides in the message rather than
        the logger name, so the logger cache never grows from an untrusted field.
        The message is raw here; the file handler's :class:`SanitizingFormatter`
        escapes the whole rendered line. The client's ``created`` is preserved.
        """
        record = logging.LogRecord(
            name=_CLIENT_LOGGER,
            level=_LEVELS.get(wire.level, logging.INFO),
            pathname="",
            lineno=0,
            msg=f"{wire.role}.{wire.name}: {wire.message}",
            args=None,
            exc_info=None,
        )
        record.created = wire.created
        return record
