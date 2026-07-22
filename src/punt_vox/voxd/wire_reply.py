"""One request's reply channel: id-stamped sends and audit-logged rejections."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

from punt_vox.log_sanitize import SANITIZER
from punt_vox.voxd._parse import safe_send

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

__all__ = ["WireReply"]

logger = logging.getLogger(__name__)

_LOG_FIELD_LIMIT = 120


class WireReply:
    """A client reply channel bound to one request's socket and id.

    Every store handler -- record, play, fetch -- replies through this one
    object. :meth:`send` stamps the request id and survives a client that has
    already disconnected; :meth:`error` additionally audit-logs the rejection at
    WARNING, so a blocked probe (a hostile record name, or a play/fetch ref that
    escapes the store or names no recording) is greppable in vox.log instead of
    silent, while a clean disconnect stays a quiet end-of-request.
    """

    __slots__ = ("_request_id", "_websocket")

    _websocket: WebSocket
    _request_id: str

    def __new__(cls, websocket: WebSocket, request_id: str) -> Self:
        self = super().__new__(cls)
        self._websocket = websocket
        self._request_id = request_id
        return self

    @property
    def request_id(self) -> str:
        """Return the wire request id this channel stamps onto every frame."""
        return self._request_id

    async def send(self, payload: dict[str, object]) -> bool:
        """Send *payload* stamped with this request's id; False if the peer had gone.

        The id is stamped *last* so a payload that happens to carry an ``id`` key
        can never override the wire request id -- the stamp always wins.
        """
        return await safe_send(self._websocket, {**payload, "id": self._request_id})

    async def error(self, message: str) -> bool:
        """Log this rejection at WARNING (sanitized) and send its error frame.

        *message* may embed an attacker-controlled name or ref, so it is
        sanitized before it reaches the log -- newlines and control characters
        are escaped and the length is capped -- which closes a log-injection
        vector into vox.log. The wire frame carries *message* verbatim. The
        rejection is logged even when the peer has gone, so the audit trail does
        not depend on the client still being there to receive the frame.
        """
        logger.warning(
            "rejected op id=%r: %s", self._request_id, self._sanitize(message)
        )
        return await self.send({"type": "error", "message": message})

    @staticmethod
    def _sanitize(message: str) -> str:
        """Return *message* escaped by the shared log sanitizer and length-capped.

        The shared :data:`SANITIZER` neutralizes the injection surface (every
        C0/C1/DEL control and Unicode line separator); the cap is applied
        *after* escaping so it bounds the actual logged field, not the pre-escape
        input a padded-out probe could inflate.
        """
        escaped = SANITIZER.escape(message)
        if len(escaped) > _LOG_FIELD_LIMIT:
            return f"{escaped[:_LOG_FIELD_LIMIT]}..."
        return escaped
