"""Tests for shared daemon speech request parsing."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from punt_vox.voxd.speech_request import SpeechRequest


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("rate", 0, "rate must be greater than 0, got 0"),
        ("rate", -10, "rate must be greater than 0, got -10"),
        ("stability", 1.5, "stability must be between 0.0 and 1.0, got 1.5"),
    ],
)
def test_rejects_invalid_spec_field(field: str, value: float, message: str) -> None:
    msg: dict[str, object] = {
        "id": "1",
        "text": "hello",
        "provider": "espeak",
        field: value,
    }
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        SpeechRequest.from_msg(msg, MagicMock())
