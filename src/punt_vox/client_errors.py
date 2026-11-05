"""Exception types raised by the voxd client and its transport."""

from __future__ import annotations

from typing import cast


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

        A host with several addresses (``localhost``: ``::1`` and
        ``127.0.0.1``) fails as an ``ExceptionGroup`` of one error per
        address; it is a refusal only when every address refused.
        """
        causes = cls._causes(exc)
        detail = "; ".join(str(cause) for cause in causes)
        msg = f"Cannot connect to voxd at {endpoint}: {detail}"
        if all(isinstance(cause, ConnectionRefusedError) for cause in causes):
            return VoxdRefusedError(msg)
        return cls(msg)

    @staticmethod
    def _causes(exc: Exception) -> tuple[Exception, ...]:
        """Return an aggregate's per-address errors, else *exc* alone."""
        if isinstance(exc, ExceptionGroup):
            # isinstance narrows to ExceptionGroup[Unknown]; an
            # ExceptionGroup's members are Exceptions by construction.
            return cast("ExceptionGroup[Exception]", exc).exceptions
        return (exc,)


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
