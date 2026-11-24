"""Tests for the ``.gitignore`` rule writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from punt_vox.gitignore_entry import VoxGitignore

if TYPE_CHECKING:
    from pathlib import Path

_ENTRY = ".punt-labs/vox/vox.local.md"
_LOCK_RULES = (
    ".punt-labs/local/",
    "/.CLAUDE.md.punt-import.lock",
    "/.claude/.settings.json.punt-import.lock",
)
_RULES = (_ENTRY, *_LOCK_RULES)


def _host(tmp_path: Path) -> Path:
    return tmp_path / ".gitignore"


def test_ensure_creates_missing_gitignore(tmp_path: Path) -> None:
    assert VoxGitignore(tmp_path).ensure() is True
    assert (
        _host(tmp_path).read_bytes()
        == b"\n".join(rule.encode() for rule in _RULES) + b"\n"
    )


@pytest.mark.parametrize(
    ("original", "appended_eol"),
    [(b"dist/", b"\n"), (b"dist/\r", b"\n"), (b"# host\r\ndist/\r", b"\r\n")],
)
def test_ensure_terminates_an_unterminated_last_rule(
    tmp_path: Path, original: bytes, appended_eol: bytes
) -> None:
    _host(tmp_path).write_bytes(original)
    VoxGitignore(tmp_path).ensure()
    rules = appended_eol.join(rule.encode() for rule in _RULES)
    assert _host(tmp_path).read_bytes() == original + b"\n" + rules + appended_eol


@pytest.mark.parametrize("existing", [_ENTRY, f"/{_ENTRY}", f"{_ENTRY}  "])
def test_ensure_is_a_noop_when_listed(tmp_path: Path, existing: str) -> None:
    original = "\n".join(("dist/", existing, *_LOCK_RULES, ""))
    _host(tmp_path).write_text(original, encoding="utf-8")
    assert VoxGitignore(tmp_path).ensure() is False
    assert _host(tmp_path).read_text(encoding="utf-8") == original


def test_ensure_adds_the_entry_after_a_leading_space_variant(tmp_path: Path) -> None:
    _host(tmp_path).write_text(f" {_ENTRY}\n", encoding="utf-8")
    assert VoxGitignore(tmp_path).ensure() is True
    text = _host(tmp_path).read_text(encoding="utf-8")
    assert text.splitlines() == [f" {_ENTRY}", *_RULES]


def test_ensure_refuses_a_symlinked_host(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n", encoding="utf-8")
    _host(tmp_path).symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        VoxGitignore(tmp_path).ensure()
    assert outside.read_text(encoding="utf-8") == "keep\n"


def test_ensure_appends_only_missing_rules(tmp_path: Path) -> None:
    original = f"/{_ENTRY}  \r\n.CLAUDE.md.punt-import.lock\r\nnode_modules/"
    _host(tmp_path).write_bytes(original.encode())
    entries = VoxGitignore(tmp_path)
    assert entries.ensure() is True
    assert entries.ensure() is False
    appended = "\r\n.punt-labs/local/\r\n/.claude/.settings.json.punt-import.lock\r\n"
    assert _host(tmp_path).read_bytes() == (original + appended).encode()
