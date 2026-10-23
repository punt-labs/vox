"""Tests for punt_vox.voxd.temp_synthesis -- a temp file no failure strands.

The cancel paths run through the pipeline in ``test_voxd_synthesis.py``; these
drive the class directly.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.temp_synthesis import TempFileSynthesis

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture(autouse=True)
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _synthesis(write: bytes, voice: str = "matilda") -> TempFileSynthesis:
    """Return a synthesis whose client writes *write* to the output file."""

    def synthesize(_request: object, path: Path) -> None:
        path.write_bytes(write)

    return _synthesis_running(synthesize, voice)


def _synthesis_running(
    synthesize: Callable[[object, Path], None], voice: str = "matilda"
) -> TempFileSynthesis:
    """Return a synthesis whose client runs *synthesize* in the worker thread."""
    spec = SynthesisSpec(provider="elevenlabs", voice=voice)
    with patch("punt_vox.voxd.temp_synthesis.TTSClient") as client_cls:
        client_cls.return_value.synthesize.side_effect = synthesize
        return TempFileSynthesis(MagicMock(), MagicMock(), spec, 5)


class _UnprintableError(Exception):
    """An error whose message cannot be rendered: ``str()`` itself raises."""

    def __str__(self) -> str:
        msg = "this error cannot be rendered"
        raise RuntimeError(msg)


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


def test_empty_output_error_cannot_forge_a_log_line() -> None:
    """The caller's voice name rides into the error the daemon logs; a raw
    newline in it would start a forged record, so it is escaped onto one line."""
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(_synthesis(b"", voice="x\nERROR forged record\ry").run())

    message = str(caught.value)
    assert "\n" not in message
    assert "\r" not in message
    assert "voice=x\\nERROR forged record\\ry" in message


@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING])
def test_cancelled_then_unprintable_failure_leaves_no_file(
    temp_dir: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    """An abandoned worker's error whose ``str()`` raises cannot keep the
    done-callback from removing the file, whether or not the log line renders."""
    entered, release = threading.Event(), threading.Event()

    def synthesize(_request: object, path: Path) -> None:
        entered.set()
        release.wait(timeout=5)
        path.write_bytes(b"written after the cancel")
        raise _UnprintableError

    async def _drive() -> None:
        task = asyncio.create_task(_synthesis_running(synthesize).run())
        await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        for _ in range(200):  # the thread finishes, then the loop cleans up
            if not list(temp_dir.iterdir()):
                return
            await asyncio.sleep(0.01)

    with caplog.at_level(level, logger="punt_vox.voxd.output_discard"):
        asyncio.run(_drive())

    assert list(temp_dir.iterdir()) == []
