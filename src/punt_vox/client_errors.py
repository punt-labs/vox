"""Exception types raised by the voxd client and its transport."""

from __future__ import annotations


class VoxdConnectionError(Exception):
    """Raised when the client cannot connect to voxd."""


class VoxdProtocolError(Exception):
    """Raised when voxd returns an unexpected response."""
