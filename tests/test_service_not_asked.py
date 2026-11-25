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
            NotAskedError.lookup_spent(),
            (
                "reading the installed bind spent the health budget before voxd "
                "could be asked; no token sent unattributed"
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
            NotAskedError.not_owned("serve.pid", 501),
            "serve.pid belongs to uid 501, not this user; no token sent unattributed",
        ),
        (
            NotAskedError.uncheckable("cannot stat /run/serve.port: denied"),
            "cannot stat /run/serve.port: denied; no token sent unattributed",
        ),
        (
            NotAskedError.republished(),
            (
                "serve.port was replaced while it was being read; "
                "no token sent unattributed"
            ),
        ),
        (
            NotAskedError.no_pid(),
            "serve.pid publishes no pid; no token sent unattributed",
        ),
        (
            NotAskedError.pid_unparseable(),
            "serve.pid does not name a pid; no token sent unattributed",
        ),
        (
            NotAskedError.publisher_gone(84805),
            (
                "voxd pid 84805 that published serve.port is gone; "
                "no token sent unattributed"
            ),
        ),
        (
            NotAskedError.publisher_foreign(84805),
            (
                "pid 84805 named in serve.pid is another user's process; "
                "no token sent unattributed"
            ),
        ),
        (
            NotAskedError.not_listening(42),
            (
                "voxd pid 42 has a socket on port 8421 but holds no listener "
                "there; no token sent"
            ),
        ),
        (
            NotAskedError.publisher_without_port(84805),
            (
                "voxd pid 84805 that published serve.port no longer holds port "
                "8421; no token sent unattributed"
            ),
        ),
        (
            NotAskedError.holder_uncheckable("cannot run lsof: timed out"),
            (
                "cannot tell whether the publisher holds port 8421: cannot run "
                "lsof: timed out; no token sent unattributed"
            ),
        ),
        (
            NotAskedError.not_voxd(84805, "/usr/bin/nc -l 8421"),
            (
                "pid 84805 named in serve.pid is not voxd (command: /usr/bin/nc -l "
                "8421); no token sent unattributed"
            ),
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
