"""Tests for strict UTF-8 speech stdin decoding."""

from __future__ import annotations

import io
import sys
from typing import TYPE_CHECKING

from punt_vox.output_formatter import OutputFormatter
from punt_vox.speech_stdin import SpeechStdin

if TYPE_CHECKING:
    import pytest


def test_read_decodes_utf8_regardless_of_stdin_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with io.TextIOWrapper(
        io.BytesIO(b"  caf\xc3\xa9\r\nvoix  \n"), encoding="latin-1"
    ) as stdin:
        monkeypatch.setattr(sys, "stdin", stdin)
        assert SpeechStdin(OutputFormatter()).read() == "café\nvoix"
