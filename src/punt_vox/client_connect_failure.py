"""One failed connect to voxd, turned into the error a caller sees.

The connect URI carries the daemon's token in its query string, and some of
websockets' errors carry the URI: ``InvalidURI`` renders as
``"<uri> isn't a valid URI: ..."``. That text reaches ``vox daemon status``,
start and restart reports, install verification and MCP replies, so a
:class:`ConnectFailure` never renders a cause that can hold the URI, and
redacts any ``token=`` value from the finished message as a backstop. Nor is
such a cause chained: a traceback renders ``__cause__`` in full, so a failure
to parse the URI is raised ``from None``.

It also decides the error's type. A refusal on every address -- nothing
listens -- is :class:`~punt_vox.client_errors.VoxdRefusedError`, the one
failure that means "not running". A listener that rejected the handshake is
:class:`~punt_vox.client_errors.VoxdHandshakeRejectedError`: something is
there. Anything else, from a timeout to an unreachable host, is a plain
:class:`~punt_vox.client_errors.VoxdConnectionError`.
"""

from __future__ import annotations

import re
from typing import ClassVar, Self, cast, final

from websockets.exceptions import InvalidURI, WebSocketException

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdHandshakeRejectedError,
    VoxdRefusedError,
)

__all__ = ["ConnectFailure"]


@final
class ConnectFailure:
    """A connect to *endpoint* that raised; render it without the token."""

    __slots__ = ("_causes", "_endpoint")

    # Any ``token=`` query value, wherever a cause put it.
    _TOKEN: ClassVar[re.Pattern[str]] = re.compile(r"token=[^&\s#'\"]+")

    _endpoint: str
    _causes: tuple[Exception, ...]

    def __new__(cls, endpoint: str, exc: Exception) -> Self:
        """*endpoint* is the URI without its query; *exc* is what connect raised.

        A host with several addresses (``localhost``: ``::1`` and
        ``127.0.0.1``) fails as an ``ExceptionGroup`` of one error per address.
        """
        self = super().__new__(cls)
        self._endpoint = endpoint
        if isinstance(exc, ExceptionGroup):
            # isinstance narrows to ExceptionGroup[Unknown]; an
            # ExceptionGroup's members are Exceptions by construction.
            self._causes = cast("ExceptionGroup[Exception]", exc).exceptions
        else:
            self._causes = (exc,)
        return self

    def error(self) -> VoxdConnectionError:
        """Return the typed error: a refusal only when every address refused."""
        detail = "; ".join(self._describe(cause) for cause in self._causes)
        msg = self._TOKEN.sub(
            "token=[REDACTED]", f"Cannot connect to voxd at {self._endpoint}: {detail}"
        )
        return self._kind()(msg)

    def chainable(self, exc: Exception) -> Exception | None:
        """Return *exc* as the error's ``__cause__``, or ``None`` to cut it.

        ``None`` is the contract, not a miss: a URI-parse cause renders the
        URI, token included, wherever the traceback is printed or logged.
        """
        if any(self._from_uri(cause) for cause in self._causes):
            return None
        return exc

    def _kind(self) -> type[VoxdConnectionError]:
        """Return the error type the causes call for."""
        if all(isinstance(cause, ConnectionRefusedError) for cause in self._causes):
            return VoxdRefusedError
        if any(self._rejected_handshake(cause) for cause in self._causes):
            return VoxdHandshakeRejectedError
        return VoxdConnectionError

    @classmethod
    def _rejected_handshake(cls, cause: Exception) -> bool:
        """Return True when a listener answered the handshake and refused it.

        ``InvalidURI`` is a WebSocketException too, but it is the client's own
        URI that failed, before anything was reached.
        """
        return isinstance(cause, WebSocketException) and not cls._from_uri(cause)

    @staticmethod
    def _from_uri(cause: Exception) -> bool:
        """Return True when *cause* is the client failing to parse its own URI.

        websockets raises ``InvalidURI``; urllib, underneath it, raises
        ``ValueError`` for a port out of range or an unbalanced ``[``.
        """
        return isinstance(cause, InvalidURI | ValueError)

    @staticmethod
    def _describe(cause: Exception) -> str:
        """Return *cause* as text that names no URI.

        ``InvalidURI`` is the one cause whose text embeds the URI; its reason
        (``cause.msg``) is kept, the URI is not. urllib's ``ValueError`` names
        only the fault.
        """
        if isinstance(cause, InvalidURI):
            return f"invalid voxd URI ({cause.msg})"
        if isinstance(cause, ValueError):
            return f"invalid voxd URI ({cause})"
        return str(cause)
