"""The voxd client's exception types: what each one tells a caller."""

from __future__ import annotations

import pytest

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdHandshakeRejectedError,
    VoxdProtocolError,
    VoxdRefusedError,
    VoxdRejectionError,
    VoxError,
)


@pytest.mark.parametrize(
    "error_type",
    [
        VoxdConnectionError,
        VoxdRefusedError,
        VoxdHandshakeRejectedError,
        VoxdProtocolError,
        VoxdRejectionError,
    ],
)
def test_one_except_voxerror_catches_every_client_failure(
    error_type: type[VoxError],
) -> None:
    with pytest.raises(VoxError):
        raise error_type("failed")


def test_a_refusal_and_a_rejected_handshake_are_connection_errors() -> None:
    """Callers that catch VoxdConnectionError keep catching both."""
    assert issubclass(VoxdRefusedError, VoxdConnectionError)
    assert issubclass(VoxdHandshakeRejectedError, VoxdConnectionError)


def test_a_rejected_handshake_is_never_a_refusal() -> None:
    """Only a refusal means 'not running'; a 403 means something is there."""
    assert not issubclass(VoxdHandshakeRejectedError, VoxdRefusedError)
    assert not issubclass(VoxdRefusedError, VoxdHandshakeRejectedError)


def test_a_rejection_frame_is_a_protocol_error_not_a_connection_error() -> None:
    assert issubclass(VoxdRejectionError, VoxdProtocolError)
    assert not issubclass(VoxdRejectionError, VoxdConnectionError)
