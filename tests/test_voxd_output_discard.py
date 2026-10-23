"""Tests for punt_vox.voxd.output_discard -- an abandoned output file never stays.

The end-to-end cancel paths run through ``test_voxd_temp_synthesis.py`` and
``test_voxd_synthesis.py``; these drive the callback on a settled future.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.output_discard import OutputDiscard

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from punt_vox.types import AudioResult

_LOGGER = "punt_vox.voxd.output_discard"


class _UnprintableError(Exception):
    """An error whose message cannot be rendered: ``str()`` itself raises."""

    def __str__(self) -> str:
        msg = "this error cannot be rendered"
        raise RuntimeError(msg)


@pytest.fixture
def output(tmp_path: Path) -> Path:
    path = tmp_path / "out.mp3"
    path.write_bytes(b"written by the abandoned worker")
    return path


def _discard_settled(
    output: Path, settle: Callable[[asyncio.Future[AudioResult]], object]
) -> None:
    """Run the discard callback on a future that *settle* completes."""

    async def _drive() -> None:
        done: asyncio.Future[AudioResult] = asyncio.get_running_loop().create_future()
        settle(done)
        OutputDiscard(output)(done)

    asyncio.run(_drive())


def test_removes_the_file_when_the_worker_was_cancelled(output: Path) -> None:
    _discard_settled(output, lambda done: done.cancel())

    assert not output.exists()


def test_removes_the_file_when_the_worker_failed(output: Path) -> None:
    _discard_settled(output, lambda done: done.set_exception(ValueError("boom")))

    assert not output.exists()


def test_unprintable_failure_with_logging_off_still_removes_the_file(
    output: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A disabled log line never renders the message, so nothing can raise."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        _discard_settled(output, lambda done: done.set_exception(_UnprintableError()))

    assert not output.exists()


def test_unprintable_failure_with_logging_on_still_removes_the_file(
    output: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """pytest's capture handler re-raises a formatting error that a production
    handler would absorb; the file goes before that error leaves the callback."""
    with (
        caplog.at_level(logging.DEBUG, logger=_LOGGER),
        pytest.raises(RuntimeError, match="cannot be rendered"),
    ):
        _discard_settled(output, lambda done: done.set_exception(_UnprintableError()))

    assert not output.exists()


def test_failure_message_is_escaped_onto_one_line(
    output: Path, caplog: pytest.LogCaptureFixture
) -> None:
    error = ValueError("voice 'x\nERROR forged record\r' not found")
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        _discard_settled(output, lambda done: done.set_exception(error))

    [message] = [r.getMessage() for r in caplog.records]
    assert message == (
        "cancelled synthesis then failed: ValueError: "
        "voice 'x\\nERROR forged record\\r' not found"
    )
