"""Exception types raised by the voxd client and its transport."""

from __future__ import annotations


class VoxError(Exception):
    """Base class for every failure a voxd client raises.

    Catch this to handle any client failure in one place; catch a subclass
    to tell a connection failure (:class:`VoxdConnectionError`) from a
    protocol failure (:class:`VoxdProtocolError`).
    """


class VoxdConnectionError(VoxError):
    """Raised when the client cannot connect to voxd.

    :class:`~punt_vox.client_connect_failure.ConnectFailure` builds it from
    what a connect raised, never carrying the URI's token.
    """


class VoxdRefusedError(VoxdConnectionError):
    """Raised when the endpoint refused the connection: nothing listens there.

    The one connect failure that says voxd is not running at the endpoint.
    """


class VoxdNotPublishedError(VoxdRefusedError):
    """Raised when no voxd has published its port: ``serve.port`` is absent.

    A cleanly stopped voxd removes the file, so nothing listens -- a refusal
    in all but name, and reported as "not running".
    """


class VoxdHandshakeRejectedError(VoxdConnectionError):
    """Raised when something on the endpoint answered the WebSocket handshake
    with a refusal of its own: an HTTP 403 for a bad token or Origin, or a
    reply that is not HTTP at all.

    Something is listening -- it is never "not running", and restarting the
    service to fix it would stop a healthy daemon.
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
