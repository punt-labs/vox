"""Tests for the vox usage-guide installer (``VoxGuidance``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.claude_md import GlobalClaudeImports
from punt_vox.guidance import VoxGuidance

_VOX = "@~/.punt-labs/vox/CLAUDE.md"


def _global(tmp_path: Path) -> GlobalClaudeImports:
    return GlobalClaudeImports(tmp_path / ".claude" / "CLAUDE.md")


def _guidance(tmp_path: Path) -> VoxGuidance:
    doc = tmp_path / "vox" / "CLAUDE.md"
    return VoxGuidance(doc, _global(tmp_path), _VOX)


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


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


def test_uninstall_prunes_import_even_when_unlink_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing doc unlink (permissions, or a race that already removed it) must
    # not skip the prune, or the global @-import would be orphaned. Both steps
    # run independently; the unlink error surfaces only after the prune ran.
    guide = _guidance(tmp_path)
    guide.install()
    global_path = tmp_path / ".claude" / "CLAUDE.md"
    assert _VOX in global_path.read_text(encoding="utf-8")

    def boom_unlink(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("simulated unlink failure")

    monkeypatch.setattr(Path, "unlink", boom_unlink)

    with pytest.raises(OSError, match="simulated unlink failure"):
        guide.uninstall()

    # The import was pruned despite the unlink failure -- self-healing teardown.
    assert _VOX not in global_path.read_text(encoding="utf-8")


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
