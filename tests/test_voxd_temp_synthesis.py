"""Tests for punt_vox.voxd.temp_synthesis -- a temp file no failure strands.

The cancel paths run through the pipeline in ``test_voxd_synthesis.py``; these
drive the class directly.
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.temp_synthesis import TempFileSynthesis

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _synthesis(write: bytes) -> TempFileSynthesis:
    """Return a synthesis whose client writes *write* to the output file."""

    def synthesize(_request: object, path: Path) -> None:
        path.write_bytes(write)

    spec = SynthesisSpec(provider="elevenlabs", voice="matilda")
    with patch("punt_vox.voxd.temp_synthesis.TTSClient") as client_cls:
        client_cls.return_value.synthesize.side_effect = synthesize
        return TempFileSynthesis(MagicMock(), MagicMock(), spec, 5)


def test_returns_the_written_file() -> None:
    path = asyncio.run(_synthesis(b"mp3 bytes").run())

    assert path.read_bytes() == b"mp3 bytes"


def test_empty_output_raises_and_leaves_no_file(temp_dir: Path) -> None:
    """Caching an empty file would poison every identical request."""
    with pytest.raises(RuntimeError, match=r"provider=elevenlabs, voice=matilda"):
        asyncio.run(_synthesis(b"").run())

    assert list(temp_dir.iterdir()) == []


def test_empty_output_names_the_caller_s_length() -> None:
    with pytest.raises(RuntimeError, match=r"size=0, chars_in=5"):
        asyncio.run(_synthesis(b"").run())
