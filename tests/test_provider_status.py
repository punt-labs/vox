"""Tests for punt_vox.providers.provider_status -- an answered request's failure."""

from __future__ import annotations

import pytest

from punt_vox.providers.provider_status import ProviderStatusError


def test_is_not_a_value_error() -> None:
    """Not a caller rejection: the wire keeps it on the opaque fault path, where
    the SDK's own error went, rather than sending the server's text verbatim."""
    assert not issubclass(ProviderStatusError, ValueError)


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (422, "bad_prompt", "provider 'elevenlabs' answered HTTP 422: bad_prompt"),
        (500, None, "provider 'elevenlabs' answered HTTP 500"),
        (None, None, "provider 'elevenlabs' answered with no HTTP status"),
    ],
    ids=["status-and-detail", "status-only", "neither"],
)
def test_str_names_the_status_and_the_server_s_message(
    status: int | None, detail: str | None, expected: str
) -> None:
    assert str(ProviderStatusError("elevenlabs", status, detail)) == expected


def test_exposes_what_the_server_said() -> None:
    error = ProviderStatusError("elevenlabs", 429, "too_many_requests")

    assert (error.provider_name, error.status_code, error.detail) == (
        "elevenlabs",
        429,
        "too_many_requests",
    )


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (500, "server melted", "server melted"),
        (500, None, "HTTP 500"),
        (None, None, "no HTTP status"),
    ],
    ids=["message", "status", "neither"],
)
def test_reason_is_the_message_or_else_the_status(
    status: int | None, detail: str | None, expected: str
) -> None:
    assert ProviderStatusError("elevenlabs", status, detail).reason == expected


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_refusal_no_retry_can_change_is_permanent(status: int) -> None:
    """A bad request, a rejected key, a forbidden or missing resource, an
    unprocessable body: the same request fails the same way every time."""
    assert ProviderStatusError("elevenlabs", status, None).is_permanent


@pytest.mark.parametrize("status", [200, 402, 408, 409, 429, 500, 502, 503, None])
def test_every_other_answer_is_transient(status: int | None) -> None:
    """A rate limit, a 5xx, an undecodable success, or no status at all may
    clear on a later try."""
    assert not ProviderStatusError("elevenlabs", status, None).is_permanent
