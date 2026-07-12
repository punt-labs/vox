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


class VoxdProtocolError(VoxError):
    """Raised when voxd returns an unexpected response."""
