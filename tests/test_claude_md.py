"""Tests for the CLAUDE.md ``@``-import registration."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
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


# ---------------------------------------------------------------------------
# register-guidance CLI command
# ---------------------------------------------------------------------------


def test_uninstall_prunes_guide_even_when_plugin_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    import subprocess

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # __main__ resolves `claude` via shutil.which and runs it via subprocess.run;
    # patch the module objects it shares with these imports.
    def fake_which(_cmd: str, *_a: object, **_k: object) -> str:
        return "/usr/bin/claude"

    monkeypatch.setattr(shutil, "which", fake_which)

    # A guide + import line exist to be pruned.
    VoxGuidance.for_current_user().install()
    doc = tmp_path / ".punt-labs" / "vox" / "CLAUDE.md"
    global_md = tmp_path / ".claude" / "CLAUDE.md"
    assert doc.is_file()
    assert _VOX in global_md.read_text(encoding="utf-8")

    # The `claude plugin uninstall` step fails (e.g. the plugin was already gone).
    def failing_run(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=1)

    monkeypatch.setattr(subprocess, "run", failing_run)

    result = CliRunner().invoke(app, ["uninstall"])

    # The command still reports the plugin failure ...
    assert result.exit_code == 1
    # ... but the guide + import were pruned anyway: uninstall is self-healing.
    assert not doc.exists()
    assert _VOX not in global_md.read_text(encoding="utf-8")


def test_register_guidance_command_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    runner = CliRunner()
    guide = tmp_path / ".punt-labs" / "vox" / "CLAUDE.md"
    global_md = tmp_path / ".claude" / "CLAUDE.md"

    result = runner.invoke(app, ["register-guidance"])
    assert result.exit_code == 0
    assert guide.is_file()
    assert _VOX in global_md.read_text(encoding="utf-8")

    result = runner.invoke(app, ["register-guidance", "--remove"])
    assert result.exit_code == 0
    assert not guide.exists()
    assert _VOX not in global_md.read_text(encoding="utf-8")
