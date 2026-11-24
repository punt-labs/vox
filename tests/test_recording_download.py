"""Tests for publishing a verified recording without clobbering local files."""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

from punt_vox.recording_download import RecordingDownload

if TYPE_CHECKING:
    from typing import IO


def _fetch(sink: IO[bytes]) -> int:
    return sink.write(b"DATA")


def test_save_publishes_the_staged_bytes(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "rec.mp3"
    staged_inodes: list[int] = []

    def fetch(sink: IO[bytes]) -> int:
        staged_inodes.append(os.fstat(sink.fileno()).st_ino)
        return _fetch(sink)

    assert RecordingDownload(dest).save(fetch) == 4
    assert dest.read_bytes() == b"DATA"
    assert [dest.stat().st_ino] == staged_inodes
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

    monkeypatch.setattr("punt_vox.recording_download.os.link", _unsupported_link)
    monkeypatch.setattr("punt_vox.recording_download.shutil.copyfileobj", failed_copy)

    with pytest.raises(type(error)) as caught:
        RecordingDownload(tmp_path / "rec.mp3").save(_fetch)
    assert caught.value is error
    assert list(tmp_path.iterdir()) == []


def _unsupported_link(*_args: object, **_kwargs: object) -> NoReturn:
    raise OSError(errno.EOPNOTSUPP, "hard links unavailable")


def test_save_falls_back_to_an_exclusive_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("punt_vox.recording_download.os.link", _unsupported_link)
    dest = tmp_path / "rec.mp3"
    assert RecordingDownload(dest).save(_fetch) == 4
    assert dest.read_bytes() == b"DATA"
    assert list(tmp_path.iterdir()) == [dest]


def test_nonprivate_directory_never_contains_a_named_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_directory = tempfile.mkdtemp

    def nonprivate_directory(**_kwargs: object) -> str:
        directory = make_directory(dir=tmp_path)
        Path(directory).chmod(0o755)
        return directory

    def fetch(sink: IO[bytes]) -> int:
        assert not list(tmp_path.iterdir())
        return _fetch(sink)

    monkeypatch.setattr(tempfile, "mkdtemp", nonprivate_directory)
    dest = tmp_path / "rec.mp3"
    assert RecordingDownload(dest).save(fetch) == 4
    assert dest.read_bytes() == b"DATA"
    assert list(tmp_path.iterdir()) == [dest]


def test_cleanup_does_not_remove_unrelated_files_after_directory_rename(
    tmp_path: Path,
) -> None:
    moved = tmp_path / "renamed"

    def fetch(sink: IO[bytes]) -> int:
        staged = next(tmp_path.iterdir())
        staged.rename(moved)
        staged.mkdir()
        (staged / "unrelated").write_bytes(b"keep")
        return _fetch(sink)

    dest = tmp_path / "rec.mp3"
    assert RecordingDownload(dest).save(fetch) == 4
    assert list(moved.iterdir()) == []
    assert next(tmp_path.glob(".vox-rec-*/unrelated")).read_bytes() == b"keep"
    assert dest.read_bytes() == b"DATA"
