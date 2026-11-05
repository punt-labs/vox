"""Exception types raised by the voxd client and its transport."""

from __future__ import annotations


class VoxError(Exception):
    """Base class for every failure a voxd client raises.

    Catch this to handle any client failure in one place; catch a subclass
    to tell a connection failure (:class:`VoxdConnectionError`) from a
    protocol failure (:class:`VoxdProtocolError`).
    """


class VoxdConnectionError(VoxError):
    """Raised when the client cannot connect to voxd."""

    @classmethod
    def of_connect(cls, endpoint: str, exc: Exception) -> VoxdConnectionError:
        """Return the error for a connect to *endpoint* that raised *exc*.

        A refusal -- nothing listens there -- is :class:`VoxdRefusedError`.
        Every other failure is a plain :class:`VoxdConnectionError`, since
        something may be there: a socket timeout, reset or unreachable host,
        or a listener that rejected the WebSocket handshake (an HTTP 403 for
        a bad token, or a peer that does not speak HTTP).
        """
        msg = f"Cannot connect to voxd at {endpoint}: {exc}"
        if isinstance(exc, ConnectionRefusedError):
            return VoxdRefusedError(msg)
        return cls(msg)


class VoxdRefusedError(VoxdConnectionError):
    """Raised when the endpoint refused the connection: nothing listens there.

    The one connect failure that says voxd is not running at the endpoint.
    """


class VoxdProtocolError(VoxError):
    """Raised when voxd returns an unexpected response."""


class VoxdRejectionError(VoxdProtocolError):
    """Raised when voxd answered with a typed ``{"type": "error"}`` frame.

    Distinct from a bare :class:`VoxdProtocolError` (malformed JSON, a
    missing key, an unexpected frame type): the daemon was reached and
    said no, carrying a caller-facing reason. A surface may render the
    reason verbatim; a bare ``VoxdProtocolError`` names a bug and
    propagates.
    """
