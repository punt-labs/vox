"""Tests for punt_vox.frontmatter -- single-file YAML frontmatter I/O."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from punt_vox.frontmatter import Frontmatter


def _boom(self: Path, *_args: object, **_kwargs: object) -> str:
    """Stand-in for ``Path.read_text`` that fails as a permission fault would."""
    raise PermissionError("permission denied")


class TestRead:
    def test_missing_file_reads_empty(self, tmp_path: Path) -> None:
        assert Frontmatter(tmp_path / "vox.md").read_fields() == {}

    def test_missing_file_field_is_none(self, tmp_path: Path) -> None:
        assert Frontmatter(tmp_path / "vox.md").read_field("voice") is None

    def test_parses_non_empty_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\nnotify: ""\n---\n')
        fm = Frontmatter(path)
        assert fm.read_fields() == {"voice": "charlie"}
        assert fm.read_field("voice") == "charlie"
        assert fm.read_field("notify") is None

    def test_unreadable_file_reads_empty_and_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An existing-but-unreadable file degrades to defaults, not a crash."""
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\n---\n')
        monkeypatch.setattr(Path, "read_text", _boom)
        with caplog.at_level(logging.WARNING, logger="punt_vox.frontmatter"):
            assert Frontmatter(path).read_fields() == {}
        messages = [r.getMessage() for r in caplog.records]
        assert any("unreadable" in m and str(path) in m for m in messages)

    def test_unreadable_file_field_is_none_and_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\n---\n')
        monkeypatch.setattr(Path, "read_text", _boom)
        with caplog.at_level(logging.WARNING, logger="punt_vox.frontmatter"):
            assert Frontmatter(path).read_field("voice") is None
        assert any(str(path) in r.getMessage() for r in caplog.records)


class TestWrite:
    def test_write_field_round_trips(self, tmp_path: Path) -> None:
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_field("voice", "fin")
        assert fm.read_field("voice") == "fin"

    def test_write_creates_parent_dir(self, tmp_path: Path) -> None:
        fm = Frontmatter(tmp_path / "a" / "b" / "vox.md")
        fm.write_field("voice", "fin")
        assert fm.read_field("voice") == "fin"

    def test_write_fields_updates_in_place(self, tmp_path: Path) -> None:
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_fields({"voice": "fin", "notify": "y"})
        fm.write_field("voice", "roger")
        assert fm.read_field("voice") == "roger"
        assert fm.read_field("notify") == "y"

    def test_malformed_file_rewritten_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "vox.md"
        path.write_text("no frontmatter fence here\n")
        with caplog.at_level(logging.WARNING, logger="punt_vox.frontmatter"):
            Frontmatter(path).write_field("voice", "fin")
        assert Frontmatter(path).read_field("voice") == "fin"
        assert any("Malformed config" in r.getMessage() for r in caplog.records)

    def test_write_fields_validates_before_touching_file(self, tmp_path: Path) -> None:
        """A corrupting value is rejected and no file is created."""
        path = tmp_path / "vox.md"
        fm = Frontmatter(path)
        with pytest.raises(ValueError, match="double-quotes"):
            fm.write_fields({"voice": "fin", "vibe": 'I"m tired'})
        assert not path.exists()

    def test_write_field_rejects_double_quote(self, tmp_path: Path) -> None:
        """The single-field path routes through the same validation."""
        path = tmp_path / "vox.md"
        with pytest.raises(ValueError, match="double-quotes"):
            Frontmatter(path).write_field("vibe", 'say "hi"')
        assert not path.exists()

    def test_write_field_accepts_apostrophe_round_trips(self, tmp_path: Path) -> None:
        """Apostrophes are safe -- mood text like ``I'm tired`` survives."""
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_field("vibe", "I'm tired")
        assert fm.read_field("vibe") == "I'm tired"


class TestValidateValue:
    @pytest.mark.parametrize("bad", ["a\nb", "a\rb"])
    def test_rejects_newlines(self, bad: str) -> None:
        with pytest.raises(ValueError, match="must not contain newlines"):
            Frontmatter.validate_value(bad)

    @pytest.mark.parametrize("bad", ['I"m tired', 'say "hi"', '"'])
    def test_rejects_double_quotes(self, bad: str) -> None:
        with pytest.raises(ValueError, match="must not contain double-quotes"):
            Frontmatter.validate_value(bad)

    def test_accepts_plain_value(self) -> None:
        Frontmatter.validate_value("charlie")  # no raise

    def test_accepts_apostrophe(self) -> None:
        Frontmatter.validate_value("I'm tired")  # no raise


class TestPath:
    def test_path_property_returns_backing_path(self, tmp_path: Path) -> None:
        path = tmp_path / "vox.md"
        assert Frontmatter(path).path == path
