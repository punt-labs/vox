"""Tests for punt_vox.service.not_asked -- why a token-bearing request stayed home."""

from __future__ import annotations

import pytest

from punt_vox.service.not_asked import NotAskedError

SENT_NOTHING = "no token sent"


@pytest.mark.parametrize(
    ("miss", "text"),
    [
        (
            NotAskedError.left_port([42, 7]),
            "voxd pid 7, 42 left port 8421 before it could be asked; no token sent",
        ),
        (
            NotAskedError.budget_spent(),
            (
                "re-checking port 8421 spent the health budget before voxd could "
                "be asked; no token sent"
            ),
        ),
        (
            NotAskedError.unpublished(),
            "serve.port publishes no port; no token sent unattributed",
        ),
        (
            NotAskedError.unparseable(),
            "serve.port does not name a port; no token sent unattributed",
        ),
        (
            NotAskedError.other_port(9000),
            (
                "serve.port names port 9000, not the service port 8421; "
                "no token sent unattributed"
            ),
        ),
        (
            NotAskedError.not_owned(501),
            "serve.port belongs to uid 501, not this user; no token sent unattributed",
        ),
        (
            NotAskedError.unreadable("cannot stat /run/serve.port: denied"),
            "cannot stat /run/serve.port: denied; no token sent unattributed",
        ),
        (
            NotAskedError.not_loopback("192.168.1.5"),
            "voxd binds 192.168.1.5, not loopback; no token sent unattributed",
        ),
        (
            NotAskedError.cannot_connect("cannot connect to 127.0.0.1:8421: x"),
            "cannot connect to 127.0.0.1:8421: x; no token sent unattributed",
        ),
        (
            NotAskedError.nothing_listens(),
            "nothing listens on port 8421; no token sent",
        ),
    ],
)
def test_each_reason_says_the_token_stayed_home(miss: NotAskedError, text: str) -> None:
    assert str(miss) == text
    assert SENT_NOTHING in str(miss)


def test_it_is_a_runtime_error_for_the_probe_to_keep() -> None:
    assert isinstance(NotAskedError.unpublished(), RuntimeError)
