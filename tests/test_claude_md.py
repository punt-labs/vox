"""Tests for the CLAUDE.md ``@``-import registration."""

from __future__ import annotations

from pathlib import Path

from punt_vox.claude_md import GlobalClaudeImports, VoxGuidance

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


# ---------------------------------------------------------------------------
# VoxGuidance
# ---------------------------------------------------------------------------


def _guidance(tmp_path: Path) -> VoxGuidance:
    doc = tmp_path / "vox" / "CLAUDE.md"
    return VoxGuidance(doc, _global(tmp_path), _VOX)


def test_install_writes_doc_and_registers(tmp_path: Path) -> None:
    guide = _guidance(tmp_path)
    guide.install()
    assert guide.doc_path.is_file()
    doc = guide.doc_path.read_text(encoding="utf-8")
    assert "mic:unmute" in doc
    global_text = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert _VOX in global_text


def test_reinstall_is_idempotent(tmp_path: Path) -> None:
    guide = _guidance(tmp_path)
    guide.install()
    global_path = tmp_path / ".claude" / "CLAUDE.md"
    first = global_path.read_text(encoding="utf-8")
    mtime = global_path.stat().st_mtime_ns
    guide.install()
    assert global_path.read_text(encoding="utf-8") == first
    assert global_path.stat().st_mtime_ns == mtime


def test_install_rewrites_stale_doc(tmp_path: Path) -> None:
    guide = _guidance(tmp_path)
    guide.doc_path.parent.mkdir(parents=True)
    guide.doc_path.write_text("stale content", encoding="utf-8")
    guide.install()
    assert "stale content" not in guide.doc_path.read_text(encoding="utf-8")


def test_doc_content_round_trips_from_asset(tmp_path: Path) -> None:
    guide = _guidance(tmp_path)
    guide.install()
    asset = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "punt_vox"
        / "assets"
        / "global-guidance.md"
    )
    assert guide.doc_path.read_text(encoding="utf-8") == asset.read_text(
        encoding="utf-8"
    )


def test_uninstall_deletes_doc_and_prunes(tmp_path: Path) -> None:
    guide = _guidance(tmp_path)
    guide.install()
    guide.uninstall()
    assert not guide.doc_path.exists()
    global_text = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert _VOX not in global_text


def test_uninstall_missing_doc_is_safe(tmp_path: Path) -> None:
    guide = _guidance(tmp_path)
    # No install first -- uninstall must not raise.
    guide.uninstall()
    assert not guide.doc_path.exists()


def test_for_current_user_import_line() -> None:
    guide = VoxGuidance.for_current_user()
    assert guide.import_line == _VOX
