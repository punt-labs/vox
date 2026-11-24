"""Tests for punt_vox.config_file -- one config file's bytes on disk."""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

import pytest

from punt_vox.config_file import ConfigFile
from punt_vox.frontmatter_block import FrontmatterBlock

_FENCED = '---\nvoice: "charlie"\n---\n'


def test_a_missing_file_reads_as_no_block(tmp_path: Path) -> None:
    assert ConfigFile(tmp_path / "vox.md").block() is None


def test_a_present_file_reads_as_its_block(tmp_path: Path) -> None:
    path = tmp_path / "vox.md"
    path.write_text(_FENCED, encoding="utf-8")
    block = ConfigFile(path).block()
    assert block is not None
    assert block.fields() == {"voice": "charlie"}


def test_the_strict_read_raises_on_a_non_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "vox.md"
    path.write_bytes(b'---\nvoice: "\xff"\n---\n')
    with pytest.raises(UnicodeDecodeError):
        ConfigFile(path).block()


def test_the_lenient_read_warns_and_reads_as_no_block(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "vox.md"
    path.write_bytes(b'---\nvoice: "\xff"\n---\n')
    with caplog.at_level(logging.WARNING, logger="punt_vox.config_file"):
        assert ConfigFile(path).readable_block() is None
    assert any(str(path) in r.getMessage() for r in caplog.records)


def test_replace_creates_the_file_and_its_directory(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "vox.md"
    ConfigFile(path).replace(FrontmatterBlock(_FENCED))
    assert path.read_text(encoding="utf-8") == _FENCED


def test_replace_lands_by_rename_and_keeps_the_mode(tmp_path: Path) -> None:
    path = tmp_path / "vox.md"
    path.write_text(_FENCED, encoding="utf-8")
    path.chmod(0o640)
    inode = path.stat().st_ino
    ConfigFile(path).replace(FrontmatterBlock('---\nvoice: "fin"\n---\n'))
    assert path.stat().st_ino != inode
    assert path.stat().st_mode & 0o777 == 0o640
    assert path.read_text(encoding="utf-8") == '---\nvoice: "fin"\n---\n'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a mode-400 file")
def test_replace_refuses_a_read_only_file(tmp_path: Path) -> None:
    """The rename needs only the directory, so the file's mode is asked first."""
    path = tmp_path / "vox.md"
    path.write_text(_FENCED, encoding="utf-8")
    path.chmod(0o400)
    with pytest.raises(PermissionError):
        ConfigFile(path).replace(FrontmatterBlock('---\nvoice: "fin"\n---\n'))
    assert path.read_text(encoding="utf-8") == _FENCED
    assert list(tmp_path.iterdir()) == [path]


def test_an_edit_names_this_file_in_its_fence_refusal(tmp_path: Path) -> None:
    path = tmp_path / "vox.md"
    edit = ConfigFile(path).edit(lambda _held: {"voice": "fin"}, lambda _f: None)
    with pytest.raises(OSError, match="no closing ---") as refused:
        edit.admit(FrontmatterBlock('---\nvoice: "charlie"\n'))
    assert refused.value.filename == str(path)


def test_the_fenced_read_refuses_an_unclosed_file_as_the_writer_does(
    tmp_path: Path,
) -> None:
    path = tmp_path / "vox.md"
    path.write_text('---\nvoice: "charlie"\n', encoding="utf-8")
    with pytest.raises(OSError, match="no closing ---") as refused:
        ConfigFile(path).fenced_block()
    assert not isinstance(refused.value, ValueError)
    assert refused.value.errno == errno.EBADMSG
    assert refused.value.filename == str(path)


def test_the_fenced_read_passes_a_fenced_or_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "vox.md"
    assert ConfigFile(path).fenced_block() is None
    path.write_text(_FENCED, encoding="utf-8")
    block = ConfigFile(path).fenced_block()
    assert block is not None
    assert block.fields() == {"voice": "charlie"}


@pytest.mark.parametrize(("umask", "mode"), [(0o077, 0o600), (0o022, 0o644)])
def test_a_new_file_honours_the_umask(tmp_path: Path, umask: int, mode: int) -> None:
    """A new config lands as a plain create would, so a 077 umask keeps it private."""
    path = tmp_path / "vox.local.md"
    previous = os.umask(umask)
    try:
        ConfigFile(path).replace(FrontmatterBlock(_FENCED))
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == mode


def test_an_existing_private_file_stays_private(tmp_path: Path) -> None:
    path = tmp_path / "vox.local.md"
    path.write_text(_FENCED, encoding="utf-8")
    path.chmod(0o600)
    previous = os.umask(0o022)
    try:
        ConfigFile(path).replace(FrontmatterBlock('---\nvoice: "fin"\n---\n'))
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o600
