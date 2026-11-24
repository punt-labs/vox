"""Tests for the ``.gitignore`` line writer (``GitignoreEntry``)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from punt_vox.gitignore_entry import GitignoreEntry

if TYPE_CHECKING:
    from pathlib import Path

_ENTRY = ".punt-labs/vox/vox.local.md"


def _host(tmp_path: Path) -> Path:
    return tmp_path / ".gitignore"


def _entry(tmp_path: Path) -> GitignoreEntry:
    return GitignoreEntry(_host(tmp_path), _ENTRY, lock_path=tmp_path / "gi.lock")


def test_ensure_creates_missing_gitignore(tmp_path: Path) -> None:
    assert _entry(tmp_path).ensure() is True
    assert _host(tmp_path).read_text(encoding="utf-8") == f"{_ENTRY}\n"


def test_ensure_appends_after_user_rules(tmp_path: Path) -> None:
    _host(tmp_path).write_text("# mine\nnode_modules/\n", encoding="utf-8")
    assert _entry(tmp_path).ensure() is True
    text = _host(tmp_path).read_text(encoding="utf-8")
    assert text == f"# mine\nnode_modules/\n{_ENTRY}\n"


def test_ensure_terminates_an_unterminated_last_rule(tmp_path: Path) -> None:
    _host(tmp_path).write_text("dist/", encoding="utf-8")
    _entry(tmp_path).ensure()
    assert _host(tmp_path).read_text(encoding="utf-8") == f"dist/\n{_ENTRY}\n"


def test_ensure_preserves_crlf(tmp_path: Path) -> None:
    _host(tmp_path).write_bytes(b"dist/\r\n")
    _entry(tmp_path).ensure()
    assert _host(tmp_path).read_bytes() == f"dist/\r\n{_ENTRY}\r\n".encode()


@pytest.mark.parametrize("existing", [_ENTRY, f"/{_ENTRY}", f"  {_ENTRY}  "])
def test_ensure_is_a_noop_when_listed(tmp_path: Path, existing: str) -> None:
    original = f"dist/\n{existing}\n"
    _host(tmp_path).write_text(original, encoding="utf-8")
    entry = _entry(tmp_path)
    assert entry.is_present() is True
    assert entry.ensure() is False
    assert _host(tmp_path).read_text(encoding="utf-8") == original


def test_ensure_twice_adds_one_line(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    entry.ensure()
    assert entry.ensure() is False
    lines = _host(tmp_path).read_text(encoding="utf-8").splitlines()
    assert lines.count(_ENTRY) == 1


@pytest.mark.subprocess
def test_ensured_entry_is_honored_by_git(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _entry(tmp_path).ensure()
    result = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "-q", _ENTRY], check=False
    )
    assert result.returncode == 0


@pytest.mark.parametrize(
    "bad", ["", " x", "x ", "a\nb", "/abs", "#comment", "!negated"]
)
def test_rejects_entries_that_would_write_another_rule(
    tmp_path: Path, bad: str
) -> None:
    with pytest.raises(ValueError, match="gitignore entry"):
        GitignoreEntry(_host(tmp_path), bad, lock_path=tmp_path / "gi.lock")


def test_entry_property(tmp_path: Path) -> None:
    assert _entry(tmp_path).entry == _ENTRY
