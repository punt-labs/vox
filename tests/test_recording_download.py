"""Tests for publishing a verified recording without clobbering local files."""

from __future__ import annotations

import errno
from typing import TYPE_CHECKING, NoReturn

import pytest

from punt_vox.recording_download import RecordingDownload

if TYPE_CHECKING:
    from pathlib import Path
    from typing import IO


def _fetch(sink: IO[bytes]) -> int:
    return sink.write(b"DATA")


def test_save_publishes_the_staged_bytes(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "rec.mp3"
    assert RecordingDownload(dest).save(_fetch) == 4
    assert dest.read_bytes() == b"DATA"
    assert list(tmp_path.iterdir()) == [dest]


def test_save_does_not_fetch_when_destination_is_a_dangling_symlink(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "rec.mp3"
    dest.symlink_to(tmp_path / "missing")

    def unexpected_fetch(_sink: IO[bytes]) -> NoReturn:
        pytest.fail("fetch called before checking a dangling destination symlink")

    with pytest.raises(FileExistsError):
        RecordingDownload(dest).save(unexpected_fetch)

    assert dest.is_symlink()
    assert list(tmp_path.iterdir()) == [dest]


@pytest.mark.parametrize(
    "error", [OSError(errno.ENOSPC, "disk full"), KeyboardInterrupt()]
)
def test_failed_copy_removes_both_partial_files(
    error: BaseException,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_copy(staged: IO[bytes], dest: IO[bytes]) -> NoReturn:
        dest.write(staged.read(2))
        raise error

    monkeypatch.setattr("punt_vox.recording_download.shutil.copyfileobj", failed_copy)

    with pytest.raises(type(error)) as caught:
        RecordingDownload(tmp_path / "rec.mp3").save(_fetch)
    assert caught.value is error
    assert list(tmp_path.iterdir()) == []
