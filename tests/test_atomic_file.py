"""Tests for the atomic, byte-preserving file writer (``AtomicFile``)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from punt_vox.atomic_file import AtomicFile


def _file(tmp_path: Path) -> AtomicFile:
    return AtomicFile(tmp_path / ".claude" / "CLAUDE.md")


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _file(tmp_path).read() == ""


@pytest.mark.parametrize(
    "raw",
    [
        b"# rules\r\n\r\nkeep me\r\n",  # CRLF
        b"# rules\rkeep me\r",  # lone CR
        b"# rules\n\nkeep me",  # LF, no final newline
    ],
)
def test_read_preserves_bytes_verbatim(tmp_path: Path, raw: bytes) -> None:
    af = _file(tmp_path)
    af.path.parent.mkdir(parents=True)
    af.path.write_bytes(raw)
    # No universal-newline translation: every ending survives read+re-encode.
    assert af.read().encode("utf-8") == raw


def test_replace_uses_sibling_temp_then_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    af = _file(tmp_path)
    af.path.parent.mkdir(parents=True)
    af.path.write_text("# original\n", encoding="utf-8")

    seen: dict[str, Path] = {}
    real_replace = Path.replace

    def spy_replace(self: Path, target: Path) -> Path:
        seen["src"] = self
        seen["dst"] = target
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy_replace)
    af.replace("# new\n")
    assert seen["dst"] == af.path
    assert seen["src"].parent == af.path.parent
    assert seen["src"] != af.path
    assert af.path.read_text(encoding="utf-8") == "# new\n"


def test_new_file_gets_default_mode(tmp_path: Path) -> None:
    af = _file(tmp_path)
    af.replace("# hi\n")
    assert stat.S_IMODE(af.path.stat().st_mode) == 0o644


def test_replace_preserves_existing_mode(tmp_path: Path) -> None:
    af = _file(tmp_path)
    af.path.parent.mkdir(parents=True)
    af.path.write_text("# rules\n", encoding="utf-8")
    af.path.chmod(0o600)
    af.replace("# changed\n")
    assert stat.S_IMODE(af.path.stat().st_mode) == 0o600


def test_replace_forces_mode_on_new_file(tmp_path: Path) -> None:
    af = _file(tmp_path)
    af.replace("# secret\n", mode=0o600)
    assert stat.S_IMODE(af.path.stat().st_mode) == 0o600


def test_replace_forces_mode_over_existing_wider_file(tmp_path: Path) -> None:
    # A forced mode wins over the default preserve-existing policy: a 0644 file
    # is narrowed to 0600 rather than kept, the invariant a secrets file needs.
    af = _file(tmp_path)
    af.path.parent.mkdir(parents=True)
    af.path.write_text("# rules\n", encoding="utf-8")
    af.path.chmod(0o644)
    af.replace("# changed\n", mode=0o600)
    assert stat.S_IMODE(af.path.stat().st_mode) == 0o600


def test_failed_chmod_leaves_original_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    af = _file(tmp_path)
    af.path.parent.mkdir(parents=True)
    original = "# keep\n"
    af.path.write_text(original, encoding="utf-8")

    def boom_chmod(self: Path, mode: int) -> None:
        raise OSError("simulated chmod failure")

    monkeypatch.setattr(Path, "chmod", boom_chmod)
    with pytest.raises(OSError, match="simulated chmod failure"):
        af.replace("# doomed\n")

    assert af.path.read_text(encoding="utf-8") == original
    assert list(af.path.parent.glob(".*.tmp")) == []


def test_failed_write_leaves_original_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    af = _file(tmp_path)
    af.path.parent.mkdir(parents=True)
    original = "# keep\n"
    af.path.write_text(original, encoding="utf-8")

    def boom_fsync(fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", boom_fsync)
    with pytest.raises(OSError, match="simulated fsync failure"):
        af.replace("# doomed\n")

    assert af.path.read_text(encoding="utf-8") == original
    assert list(af.path.parent.glob(".*.tmp")) == []


def test_fdopen_failure_cleanup_never_masks_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fdopen-failure path unlinks its temp under contextlib.suppress, so a
    # raising unlink cannot mask the real os.fdopen error -- parity with the main
    # cleanup path. Before the suppress was added, the cleanup's "unlink refused"
    # would propagate instead of the true "fdopen refused" cause.
    af = _file(tmp_path)
    af.path.parent.mkdir(parents=True)
    original = "# keep\n"
    af.path.write_text(original, encoding="utf-8")

    def boom_fdopen(*_args: object, **_kwargs: object) -> None:
        raise OSError("fdopen refused")

    def boom_unlink(_self: Path, missing_ok: bool = False) -> None:
        raise OSError("unlink refused")

    monkeypatch.setattr(os, "fdopen", boom_fdopen)
    monkeypatch.setattr(Path, "unlink", boom_unlink)
    with pytest.raises(OSError, match="fdopen refused"):
        af.replace("# doomed\n")

    # The real cause surfaced, and the original file is untouched.
    assert af.path.read_text(encoding="utf-8") == original


def test_symlink_target_is_rewritten_and_link_preserved(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    real = store / "CLAUDE.md"
    real.write_text("# real\n", encoding="utf-8")

    link = tmp_path / ".claude" / "CLAUDE.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    AtomicFile(link).replace("# via link\n")

    assert link.is_symlink()
    assert link.readlink() == real
    assert real.read_text(encoding="utf-8") == "# via link\n"
    assert list(link.parent.glob(".*.tmp")) == []
    assert list(real.parent.glob(".*.tmp")) == []
