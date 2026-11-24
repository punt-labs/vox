"""Tests for leading expressive tags and session vibe application."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.resolve import (
    apply_vibe,
    split_leading_expressive_tags,
    strip_expressive_tags,
)

if TYPE_CHECKING:
    import pytest


class TestApplyVibe:
    """Tests for apply_vibe text injection."""

    def test_prepends_tags(self, hermetic_config: Path) -> None:
        local_md = hermetic_config / "vox.local.md"
        local_md.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=True)
        assert result == "[excited] Hello world"

    def test_multiple_tags(self, hermetic_config: Path) -> None:
        (hermetic_config / "vox.local.md").write_text(
            '---\nvibe_tags: "[frustrated] [sighs]"\n---\n'
        )
        result = apply_vibe("Hello world", expressive_tags=True)
        assert result == "[frustrated] [sighs] Hello world"

    def test_skips_prepend_when_text_starts_with_tag(
        self, hermetic_config: Path
    ) -> None:
        (hermetic_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[calm] Already tagged", expressive_tags=True)
        assert result == "[calm] Already tagged"

    def test_skips_prepend_when_text_starts_with_different_tag(
        self, hermetic_config: Path
    ) -> None:
        (hermetic_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[excited] Different tag", expressive_tags=True)
        assert result == "[excited] Different tag"

    def test_skips_prepend_when_tag_contains_punctuation(
        self, hermetic_config: Path
    ) -> None:
        (hermetic_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[dramatic tone] Something important", expressive_tags=True)
        assert result == "[dramatic tone] Something important"

    def test_passthrough_when_no_tags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.config as cfg

        missing = tmp_path / "missing_dir"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_DIR", missing)
        assert apply_vibe("Hello world", expressive_tags=True) == "Hello world"

    def test_skips_tags_when_not_supported(self, hermetic_config: Path) -> None:
        local_md = hermetic_config / "vox.local.md"
        local_md.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=False)
        assert result == "Hello world"

    def test_strips_leading_tags_when_not_supported(
        self, hermetic_config: Path
    ) -> None:
        result = apply_vibe("[serious] Hello world", expressive_tags=False)
        assert result == "Hello world"

    def test_strips_multiple_leading_tags_when_not_supported(
        self, hermetic_config: Path
    ) -> None:
        result = apply_vibe("[serious] [calm] Hello world", expressive_tags=False)
        assert result == "Hello world"

    def test_preserves_text_when_only_tags(self, hermetic_config: Path) -> None:
        result = apply_vibe("[serious]", expressive_tags=False)
        assert result == "[serious]"

    def test_override_tags_win_over_config(self, hermetic_config: Path) -> None:
        (hermetic_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe(
            "Hello world",
            expressive_tags=True,
            override_tags="[excited]",
        )
        assert result == "[excited] Hello world"

    def test_override_tags_empty_falls_through_to_config(
        self, hermetic_config: Path
    ) -> None:
        (hermetic_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=True, override_tags="")
        assert result == "[calm] Hello world"


class TestStripExpressiveTags:
    """Tests for strip_expressive_tags — the underlying primitive."""

    def test_strips_single_leading_tag(self) -> None:
        assert strip_expressive_tags("[serious] Hello world") == "Hello world"

    def test_strips_multiple_leading_tags(self) -> None:
        assert strip_expressive_tags("[serious] [calm] Hello world") == "Hello world"

    def test_strips_tag_with_leading_whitespace(self) -> None:
        assert strip_expressive_tags("   [excited] Hello") == "Hello"

    def test_strips_tag_with_punctuation_inside(self) -> None:
        assert (
            strip_expressive_tags("[dramatic tone] Important message")
            == "Important message"
        )

    def test_passes_through_text_with_no_tags(self) -> None:
        assert strip_expressive_tags("Hello world") == "Hello world"

    def test_only_strips_leading_tags_not_embedded(self) -> None:
        assert strip_expressive_tags("Hello [serious] world") == "Hello [serious] world"

    def test_returns_original_when_stripping_would_empty_text(self) -> None:
        # Degenerate case: text was nothing but tags. Returning empty would
        # produce silence, which is worse than speaking the literal text.
        assert strip_expressive_tags("[serious]") == "[serious]"

    def test_returns_original_when_stripping_would_leave_only_whitespace(self) -> None:
        assert strip_expressive_tags("[serious]   ") == "[serious]   "

    def test_empty_string_passes_through(self) -> None:
        assert strip_expressive_tags("") == ""


class TestSplitLeadingExpressiveTags:
    """Tests for split_leading_expressive_tags — the (tags, body) splitter."""

    def test_splits_single_leading_tag(self) -> None:
        tags, body = split_leading_expressive_tags("[serious] Hello world")
        assert tags == "[serious]"
        # _LEADING_TAGS_RE consumes the whitespace after the bracket
        # via the trailing \s* in its pattern.
        assert body == "Hello world"

    def test_splits_multiple_leading_tags(self) -> None:
        tags, body = split_leading_expressive_tags("[serious] [calm] Hello world")
        assert tags == "[serious] [calm]"
        assert body == "Hello world"

    def test_splits_tag_with_leading_whitespace(self) -> None:
        tags, body = split_leading_expressive_tags("   [excited] Hello")
        assert tags == "[excited]"
        assert body == "Hello"

    def test_splits_tag_with_punctuation_inside(self) -> None:
        tags, body = split_leading_expressive_tags("[dramatic tone] Important message")
        assert tags == "[dramatic tone]"
        assert body == "Important message"

    def test_no_tags_returns_empty_tags_and_full_text(self) -> None:
        tags, body = split_leading_expressive_tags("Hello world")
        assert tags == ""
        assert body == "Hello world"

    def test_only_splits_leading_not_embedded(self) -> None:
        tags, body = split_leading_expressive_tags("Hello [serious] world")
        assert tags == ""
        assert body == "Hello [serious] world"

    def test_tags_only_input(self) -> None:
        tags, body = split_leading_expressive_tags("[serious]")
        assert tags == "[serious]"
        assert body == ""

    def test_empty_string(self) -> None:
        tags, body = split_leading_expressive_tags("")
        assert tags == ""
        assert body == ""
