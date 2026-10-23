"""Tests for punt_vox.providers.error_body -- the reason a JSON error body gives."""

from __future__ import annotations

import pytest

from punt_vox.providers.error_body import ErrorBody


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"detail": "voice not found"}, "voice not found"),
        ({"detail": {"message": "names an artist"}}, "names an artist"),
        (
            {"detail": {"status": "bad_prompt", "message": "names an artist"}},
            "names an artist",
        ),
        ({"detail": {"status": "quota_exceeded"}}, "quota_exceeded"),
        ({"detail": {"status": "quota_exceeded", "message": ""}}, "quota_exceeded"),
        (
            {"detail": [{"loc": ["body", "text"], "msg": "field required"}]},
            "field required",
        ),
        (
            {"detail": [{"msg": "first"}, {"msg": "second"}]},
            "first",
        ),
    ],
    ids=[
        "detail-str",
        "detail-message",
        "message-before-status",
        "detail-status",
        "empty-message-falls-to-status",
        "detail-list",
        "detail-list-first",
    ],
)
def test_message_is_the_server_s_reason(body: object, expected: str) -> None:
    assert ErrorBody(body).message == expected


_FORGED = "voice not found\nERROR punt_vox forged\r\x1b[31m"


@pytest.mark.parametrize(
    "body",
    [
        {"detail": _FORGED},
        {"detail": {"message": _FORGED}},
        {"detail": {"status": _FORGED}},
        {"detail": [{"msg": _FORGED}]},
    ],
    ids=["detail-str", "detail-message", "detail-status", "detail-list"],
)
def test_message_escapes_control_characters(body: object) -> None:
    """The message is written to the log; a raw newline would forge a record."""
    assert ErrorBody(body).message == (
        "voice not found\\nERROR punt_vox forged\\r\\x1b[31m"
    )


@pytest.mark.parametrize(
    "body",
    [
        None,
        "<html>502 Bad Gateway</html>",
        b"raw bytes",
        {},
        {"detail": ""},
        {"detail": {"message": ""}},
        {"detail": {"message": 7}},
        {"detail": []},
        {"detail": ["not a mapping"]},
        {"detail": [{"msg": ""}]},
        {"error": "not under detail"},
    ],
    ids=[
        "none",
        "raw-text",
        "raw-bytes",
        "empty-dict",
        "empty-detail",
        "empty-message",
        "non-str-message",
        "empty-list",
        "list-of-str",
        "empty-msg",
        "no-detail-key",
    ],
)
def test_a_body_with_no_reason_has_no_message(body: object) -> None:
    """Raw text is never echoed: it can be an HTML page or a proxy's banner."""
    assert ErrorBody(body).message is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"detail": {"status": "invalid_api_key"}}, "invalid_api_key"),
        (
            {"detail": {"status": "detected_unusual_activity"}},
            "detected_unusual_activity",
        ),
        ({"detail": {"status": "quota-exceeded.v2"}}, "quota-exceeded.v2"),
        ({"detail": {"status": "x" * 64}}, "x" * 64),
    ],
    ids=["snake", "long-snake", "dash-dot", "longest"],
)
def test_status_is_the_server_s_identifier(body: object, expected: str) -> None:
    assert ErrorBody(body).status == expected


@pytest.mark.parametrize(
    "body",
    [
        None,
        "invalid_api_key",
        {"detail": "invalid_api_key"},
        {"detail": {"message": "Invalid API key"}},
        {"detail": {"status": ""}},
        {"detail": {"status": "has a space"}},
        {"detail": {"status": "key sk-abc123\nleaked"}},
        {"detail": {"status": "invalid_api_key\n"}},
        {"detail": {"status": "x" * 65}},
        {"detail": {"status": 401}},
    ],
    ids=[
        "none",
        "raw-text",
        "detail-str",
        "message-only",
        "empty",
        "space",
        "newline",
        "trailing-newline",
        "too-long",
        "non-str",
    ],
)
def test_a_status_that_is_not_an_identifier_is_withheld(body: object) -> None:
    """The status goes to the log; only an identifier is safe to write there."""
    assert ErrorBody(body).status is None
