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
