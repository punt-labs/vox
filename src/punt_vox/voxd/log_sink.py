"""Receive a shipped client log frame and re-emit it into the daemon's ``vox.log``.

The single-writer daemon owns the one durable log; every client ships its records
here over the WebSocket. This handler reconstructs each frame as a role-tagged
:class:`logging.LogRecord` and hands it to the matching logger, so a client line
lands in ``vox.log`` beside the daemon's own -- one file, one writer, no
multi-process rotation race.

Two safety rules hold at this write boundary: the record is emitted with
``args=None`` so an untrusted value can never be re-interpolated into a second
record, and the shipped message is :data:`SANITIZER`-escaped before the file
handler writes it, so a smuggled newline cannot forge a second physical line.
A frame that fails to parse is refused with a metadata-only WARNING -- the field
and type that were wrong, never the payload.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.log_sanitize import SANITIZER
from punt_vox.log_wire import LogRecordWire
from punt_vox.voxd.types import MessageHandler

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

__all__ = ["LogHandler"]

logger = logging.getLogger(__name__)

_LEVELS = logging.getLevelNamesMapping()


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
        logging.getLogger(wire.qualified_name).handle(self._reconstruct(wire))

    @staticmethod
    def _reconstruct(wire: LogRecordWire) -> logging.LogRecord:
        """Build a role-tagged record: escaped message, ``args=None``, client time.

        ``args=None`` means ``getMessage`` returns the message verbatim (no second
        interpolation); the escape closes the file-write line-forging gap; the
        client's ``created`` preserves the original timestamp.
        """
        record = logging.LogRecord(
            name=wire.qualified_name,
            level=_LEVELS.get(wire.level, logging.INFO),
            pathname="",
            lineno=0,
            msg=SANITIZER.escape(wire.message),
            args=None,
            exc_info=None,
        )
        record.created = wire.created
        return record
