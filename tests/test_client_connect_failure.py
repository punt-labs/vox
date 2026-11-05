"""``ConnectFailure``: a failed connect, typed and rendered without the token."""

from __future__ import annotations

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidMessage, InvalidStatus, InvalidURI
from websockets.http11 import Response

from punt_vox.client_connect_failure import ConnectFailure
from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdHandshakeRejectedError,
    VoxdRefusedError,
)

_ENDPOINT = "ws://box:8421/ws"
_SECRET = "SEKRIT-token-0451"


def _refused() -> ConnectionRefusedError:
    return ConnectionRefusedError(111, "Connection refused")


def _forbidden() -> InvalidStatus:
    return InvalidStatus(Response(403, "Forbidden", Headers(), b""))


def test_a_single_refusal_is_a_refusal() -> None:
    error = ConnectFailure(_ENDPOINT, _refused()).error()
    assert type(error) is VoxdRefusedError
    assert (
        str(error)
        == f"Cannot connect to voxd at {_ENDPOINT}: [Errno 111] Connection refused"
    )


def test_every_address_refused_is_a_refusal() -> None:
    """localhost resolves to ::1 and 127.0.0.1; both refusing is not running."""
    group = ExceptionGroup("create_connection failed", [_refused(), _refused()])
    error = ConnectFailure("ws://localhost:8421/ws", group).error()
    assert type(error) is VoxdRefusedError
    assert "Connection refused" in str(error)


def test_one_address_failing_otherwise_is_not_a_refusal() -> None:
    unreachable = OSError(113, "No route to host")
    group = ExceptionGroup("create_connection failed", [_refused(), unreachable])
    error = ConnectFailure(_ENDPOINT, group).error()
    assert type(error) is VoxdConnectionError
    assert "No route to host" in str(error)


@pytest.mark.parametrize(
    "rejection", [_forbidden(), InvalidMessage("did not receive a valid HTTP response")]
)
def test_a_rejected_handshake_is_its_own_type(rejection: Exception) -> None:
    error = ConnectFailure(_ENDPOINT, rejection).error()
    assert type(error) is VoxdHandshakeRejectedError
    assert not isinstance(error, VoxdRefusedError)


def test_a_timeout_is_a_plain_connection_error() -> None:
    error = ConnectFailure(_ENDPOINT, TimeoutError("timed out")).error()
    assert type(error) is VoxdConnectionError


def test_an_invalid_uri_keeps_its_reason_and_drops_the_uri() -> None:
    uri = f"ws://::1:8421/ws?token={_SECRET}"
    error = ConnectFailure(
        "ws://::1:8421/ws", InvalidURI(uri, "hostname isn't provided")
    ).error()
    assert _SECRET not in str(error)
    assert "invalid voxd URI (hostname isn't provided)" in str(error)
    assert type(error) is VoxdConnectionError


def test_any_token_a_cause_carries_is_redacted() -> None:
    """The backstop: a cause that renders a URI still never shows the token."""
    leaky = OSError(f"cannot reach ws://box:8421/ws?token={_SECRET}&v=1")
    message = str(ConnectFailure(_ENDPOINT, leaky).error())
    assert _SECRET not in message
    assert "token=[REDACTED]&v=1" in message
