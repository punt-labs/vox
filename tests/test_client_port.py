"""Decimal port parsing at the client discovery boundary."""

from __future__ import annotations

import pytest

from punt_vox.client_port import PortText


@pytest.mark.parametrize(
    ("text", "port"), [("1", 1), ("65535", 65535), ("\uff10\uff11", 1)]
)
def test_parse_a_decimal_port(text: str, port: int) -> None:
    assert PortText.parse(text) == port


@pytest.mark.parametrize("text", ["", " ", " 1 ", "+1", "-1", "1_000", "0", "65536"])
def test_reject_a_non_port(text: str) -> None:
    with pytest.raises(ValueError, match=r"is not a port \(1-65535\)"):
        PortText.parse(text)


def test_preview_only_the_start_of_an_oversized_decimal() -> None:
    with pytest.raises(ValueError) as caught:
        PortText.parse("9" * 4301)
    assert str(caught.value) == f"{'9' * 32!r}... is not a port (1-65535)"
