"""Tests for the CLAUDE.md ``@``-import reconciler (``GlobalClaudeImports``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from punt_vox.claude_md import GlobalClaudeImports

_OPEN = "<!-- punt:mandatory-reading -->"
_CLOSE = "<!-- /punt:mandatory-reading -->"
_HEADER = "## Tool Guidance (auto-loaded)"
_VOX = "@~/.punt-labs/vox/CLAUDE.md"


def _global(tmp_path: Path) -> GlobalClaudeImports:
    return GlobalClaudeImports(tmp_path / ".claude" / "CLAUDE.md")


# ---------------------------------------------------------------------------
# GlobalClaudeImports.register
# ---------------------------------------------------------------------------


def test_register_creates_file_with_section(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    wrote = reg.register(_VOX)
    assert wrote is True
    text = reg.path.read_text(encoding="utf-8")
    assert _OPEN in text
    assert _CLOSE in text
    assert _HEADER in text
    assert _VOX in text


def test_register_is_idempotent_no_op_unchanged(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    assert reg.register(_VOX) is True
    first = reg.path.read_text(encoding="utf-8")
    mtime = reg.path.stat().st_mtime_ns
    # Second registration must not rewrite the file at all.
    assert reg.register(_VOX) is False
    assert reg.path.read_text(encoding="utf-8") == first
    assert reg.path.stat().st_mtime_ns == mtime


def test_register_preserves_existing_content(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    existing = (
        "# My rules\n\n<!-- quarry:capabilities -->\n"
        "quarry\n<!-- /quarry:capabilities -->\n"
    )
    reg.path.write_text(existing, encoding="utf-8")
    reg.register(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    assert "# My rules" in text
    assert "<!-- quarry:capabilities -->" in text
    assert "quarry" in text
    assert _VOX in text


def test_register_sorts_imports_deterministically(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.register("@~/z-tool.md")
    reg.register("@~/a-tool.md")
    reg.register(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.startswith("@")]
    assert lines == sorted(lines)
    assert lines == [_VOX, "@~/a-tool.md", "@~/z-tool.md"]


def test_registered_import_is_top_level_not_in_fence(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.register(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    # The import line must not sit inside a code fence -- fenced imports are
    # not resolved by Claude Code.
    assert "```" not in text
    for line in text.splitlines():
        if line.strip() == _VOX:
            assert line == _VOX  # no leading indentation / prefix


# ---------------------------------------------------------------------------
# GlobalClaudeImports corruption repair
# ---------------------------------------------------------------------------


def test_lone_open_marker_is_repaired_not_duplicated(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    # A section whose close marker was lost (half-written / hand-edited).
    corrupt = f"# rules\n\n{_OPEN}\n{_HEADER}\n\n{_VOX}\n"
    reg.path.write_text(corrupt, encoding="utf-8")
    reg.register(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    assert text.count(_OPEN) == 1
    assert text.count(_CLOSE) == 1
    assert text.count(_VOX) == 1


def test_stray_close_marker_is_removed(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    reg.path.write_text(f"# rules\n\n{_CLOSE}\n", encoding="utf-8")
    reg.register(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    assert text.count(_CLOSE) == 1
    assert text.count(_OPEN) == 1


def test_duplicate_sections_collapse_to_one(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    doubled = (
        f"{_OPEN}\n{_HEADER}\n\n@~/a.md\n{_CLOSE}\n\n"
        f"{_OPEN}\n{_HEADER}\n\n@~/b.md\n{_CLOSE}\n"
    )
    reg.path.write_text(doubled, encoding="utf-8")
    reg.register(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    assert text.count(_OPEN) == 1
    assert text.count(_CLOSE) == 1
    assert "@~/a.md" in text
    assert "@~/b.md" in text
    assert _VOX in text


# ---------------------------------------------------------------------------
# GlobalClaudeImports fence-aware parsing
# ---------------------------------------------------------------------------


def test_marker_inside_code_fence_is_preserved(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    # A tool documents this very feature: the marker + import live inside a
    # fenced block and must survive byte-for-byte, fence and all.
    fenced = (
        "# My rules\n\n"
        "Register like this:\n\n"
        "```markdown\n"
        f"{_OPEN}\n"
        f"{_HEADER}\n\n"
        f"{_VOX}\n"
        f"{_CLOSE}\n"
        "```\n"
    )
    reg.path.write_text(fenced, encoding="utf-8")
    reg.register("@~/real-tool.md")
    text = reg.path.read_text(encoding="utf-8")
    # The fenced block is intact: markers, import line, and both fences.
    assert fenced.rstrip("\n") in text
    assert "```markdown" in text
    assert text.count("```") == 2
    # The fenced marker was NOT consumed as a managed section.
    assert text.count(_OPEN) == 2  # one fenced (literal) + one real section
    # The real registration reconciled outside the fence.
    assert "@~/real-tool.md" in text


def test_fenced_marker_not_treated_as_registration(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    # Only a fenced marker exists -- there is no real managed section, so the
    # fenced import must not be harvested into a new canonical section.
    fenced = f"# doc\n\n```\n{_OPEN}\n{_VOX}\n{_CLOSE}\n```\n"
    reg.path.write_text(fenced, encoding="utf-8")
    # Pruning the vox line is a no-op: the only occurrence is fenced text.
    assert reg.prune(_VOX) is False
    assert reg.path.read_text(encoding="utf-8") == fenced


def test_tilde_fence_protects_marker(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    fenced = f"# doc\n\n~~~\n{_OPEN}\n{_VOX}\n{_CLOSE}\n~~~\n"
    reg.path.write_text(fenced, encoding="utf-8")
    reg.register("@~/other.md")
    text = reg.path.read_text(encoding="utf-8")
    assert "~~~" in text
    assert text.count(_OPEN) == 2  # fenced literal + real section
    assert "@~/other.md" in text


# ---------------------------------------------------------------------------
# GlobalClaudeImports atomic write
# ---------------------------------------------------------------------------


def test_write_uses_temp_then_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    reg.path.write_text("# original\n", encoding="utf-8")

    seen: dict[str, Path] = {}
    real_replace = Path.replace

    def spy_replace(self: Path, target: Path) -> Path:
        # The source must be a sibling temp file, never the target itself.
        seen["src"] = self
        seen["dst"] = target
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy_replace)
    assert reg.register(_VOX) is True
    assert seen["dst"] == reg.path
    assert seen["src"].parent == reg.path.parent
    assert seen["src"] != reg.path
    assert _VOX in reg.path.read_text(encoding="utf-8")


def test_interrupted_write_leaves_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    original = "# hand-authored rules\n\nkeep me\n"
    reg.path.write_text(original, encoding="utf-8")

    def boom(self: Path, target: Path) -> Path:
        raise OSError("simulated crash mid-replace")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        reg.register(_VOX)

    # The original file is untouched -- no truncation, no partial content.
    assert reg.path.read_text(encoding="utf-8") == original
    # No temp file was left behind in the directory.
    leftovers = list(reg.path.parent.glob(".claude-md-*.tmp"))
    assert leftovers == []


def test_fdopen_failure_closes_fd_and_leaves_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If os.fdopen raises, it never took ownership of the mkstemp fd; the raw
    # descriptor must be closed explicitly (no leak) and the temp unlinked.
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    original = "# keep\n"
    reg.path.write_text(original, encoding="utf-8")

    closed: list[int] = []
    real_close = os.close

    def spy_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def boom_fdopen(fd: int, *args: object, **kwargs: object) -> object:
        raise OSError("simulated fdopen failure")

    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(os, "fdopen", boom_fdopen)

    with pytest.raises(OSError, match="simulated fdopen failure"):
        reg.register(_VOX)

    assert closed, "the raw fd was leaked -- os.close was never called"
    assert reg.path.read_text(encoding="utf-8") == original
    assert list(reg.path.parent.glob(".claude-md-*.tmp")) == []


def test_in_write_failure_leaves_no_temp_and_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failure inside the ``with handle`` block (write/flush/fsync) is caught,
    # the temp unlinked, and the original left intact; the owned fd is closed by
    # the ``with``. os.fsync stands in for the write/flush/fsync sequence.
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    original = "# keep\n"
    reg.path.write_text(original, encoding="utf-8")

    def boom_fsync(fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", boom_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        reg.register(_VOX)

    assert reg.path.read_text(encoding="utf-8") == original
    assert list(reg.path.parent.glob(".claude-md-*.tmp")) == []


# ---------------------------------------------------------------------------
# GlobalClaudeImports.prune
# ---------------------------------------------------------------------------


def test_prune_removes_section_when_last_import(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.register(_VOX)
    assert reg.prune(_VOX) is True
    text = reg.path.read_text(encoding="utf-8")
    assert _OPEN not in text
    assert _VOX not in text


def test_prune_keeps_section_when_other_imports_remain(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.register("@~/other.md")
    reg.register(_VOX)
    reg.prune(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    assert _VOX not in text
    assert "@~/other.md" in text
    assert _OPEN in text


def test_uninstall_restores_no_section_file_byte_for_byte(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    # A user file that intentionally ends in trailing blank lines. Registering
    # then pruning the only import must leave content outside the managed
    # section untouched -- including those trailing blanks and the exact ending.
    original = "# My rules\n\nkeep me\n\n\n"
    reg.path.write_text(original, encoding="utf-8")
    assert reg.register(_VOX) is True
    assert reg.prune(_VOX) is True
    assert reg.path.read_text(encoding="utf-8") == original


def test_uninstall_restores_no_final_newline_byte_for_byte(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    # A user file with NO trailing newline. Register injects a separator newline
    # to anchor the section; prune must strip exactly that newline back off, so
    # the round-trip is byte-identical -- no final newline stays no final newline.
    original = "# My rules\n\nkeep me"
    reg.path.write_text(original, encoding="utf-8")
    assert reg.register(_VOX) is True
    assert reg.prune(_VOX) is True
    assert reg.path.read_text(encoding="utf-8") == original


def test_register_preserves_trailing_blank_lines(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.path.parent.mkdir(parents=True)
    # The user's trailing blank lines survive registration verbatim; only the
    # machine-owned section is appended after them.
    reg.path.write_text("# rules\n\ncontent\n\n\n", encoding="utf-8")
    reg.register(_VOX)
    text = reg.path.read_text(encoding="utf-8")
    assert text.startswith("# rules\n\ncontent\n\n\n")
    assert _VOX in text


def test_prune_absent_line_is_no_op(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    reg.register("@~/other.md")
    before = reg.path.read_text(encoding="utf-8")
    assert reg.prune(_VOX) is False
    assert reg.path.read_text(encoding="utf-8") == before


def test_prune_missing_file_is_no_op(tmp_path: Path) -> None:
    reg = _global(tmp_path)
    assert reg.prune(_VOX) is False
    assert not reg.path.exists()
