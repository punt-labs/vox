"""Tests for punt_vox.frontmatter_block -- the YAML frontmatter grammar."""

from __future__ import annotations

import pytest

from punt_vox.frontmatter_block import FrontmatterBlock
from punt_vox.types_errors import ConfigValueError

_FENCED = '---\nvoice: "charlie"\nnotify: ""\n---\n'
_BODY_FIELDS = '\n# Notes\n\nnotify: "y"\nvoice: "fin"\n'
# Frontmatter followed by prose that carries a markdown horizontal rule --
# the shape a deposited guide takes once a user writes notes under it.
_FENCED_WITH_PROSE = f"{_FENCED}\n# Notes\n\nabove the rule\n\n---\n\nbelow it\n"
# Prose with a horizontal rule and no frontmatter at all.
_PROSE_WITH_RULE = "# Notes\n\nabove the rule\n\n---\n\nbelow it\n"


class TestRendered:
    def test_builds_a_fenced_block(self) -> None:
        block = FrontmatterBlock.rendered({"voice": "fin", "notify": "y"})
        assert block.text == '---\nvoice: "fin"\nnotify: "y"\n---\n'

    def test_round_trips_through_fields(self) -> None:
        block = FrontmatterBlock.rendered({"voice": "fin"})
        assert block.fields() == {"voice": "fin"}


class TestFields:
    def test_skips_empty_values(self) -> None:
        assert FrontmatterBlock(_FENCED).fields() == {"voice": "charlie"}

    def test_a_field_reads_back(self) -> None:
        assert FrontmatterBlock(_FENCED).field("voice") == "charlie"

    def test_an_empty_field_is_unset(self) -> None:
        assert FrontmatterBlock(_FENCED).field("notify") is None

    def test_an_absent_field_is_unset(self) -> None:
        assert FrontmatterBlock(_FENCED).field("provider") is None

    def test_body_fields_do_not_override_frontmatter(self) -> None:
        block = FrontmatterBlock('---\nnotify: "n"\n---\n' + _BODY_FIELDS)
        assert block.fields() == {"notify": "n"}
        assert block.field("notify") == "n"

    def test_field_ignores_body_only_key_after_an_empty_block(self) -> None:
        assert FrontmatterBlock("---\n---\n" + _BODY_FIELDS).field("voice") is None

    def test_field_agrees_with_bulk_read_for_duplicate_keys(self) -> None:
        block = FrontmatterBlock('---\nnotify: "n"\nnotify: "y"\nnotify: ""\n---')
        assert block.field("notify") == block.fields()["notify"] == "y"

    def test_empty_bare_field_does_not_consume_the_following_field(self) -> None:
        block = FrontmatterBlock("---\nvoice:\nnotify: c\n---\n")
        assert block.field("notify") == "c"
        assert block.fields() == {"notify": "c"}
        assert block.with_fields({"voice": "fin"}).fields() == {
            "notify": "c",
            "voice": "fin",
        }


class TestAccepts:
    def test_a_fenced_block_takes_anything(self) -> None:
        assert FrontmatterBlock(_FENCED).accepts(["provider"]) is True

    def test_an_unfenced_block_takes_a_key_it_already_holds(self) -> None:
        # No fence to insert above, but the field is there to be replaced --
        # so the edit lands in place rather than rewriting the file whole.
        assert FrontmatterBlock('voice: "charlie"\n').accepts(["voice"]) is True

    def test_an_unfenced_block_refuses_a_key_it_does_not_hold(self) -> None:
        assert FrontmatterBlock('voice: "charlie"\n').accepts(["notify"]) is False

    def test_prose_refuses_everything(self) -> None:
        assert FrontmatterBlock("no frontmatter here\n").accepts(["voice"]) is False

    def test_a_horizontal_rule_in_prose_is_not_a_fence(self) -> None:
        """A ``---`` rule below prose opens nothing, so it closes nothing."""
        assert FrontmatterBlock(_PROSE_WITH_RULE).accepts(["voice"]) is False


class TestWithFields:
    def test_replaces_a_field_in_place(self) -> None:
        updated = FrontmatterBlock(_FENCED).with_fields({"voice": "roger"})
        assert updated.field("voice") == "roger"

    def test_inserts_an_absent_field_above_the_fence(self) -> None:
        updated = FrontmatterBlock(_FENCED).with_fields({"provider": "openai"})
        assert updated.field("provider") == "openai"
        assert updated.text == (
            '---\nvoice: "charlie"\nnotify: ""\nprovider: "openai"\n---\n'
        )

    def test_keeps_the_fields_it_was_not_asked_about(self) -> None:
        updated = FrontmatterBlock(_FENCED).with_fields({"provider": "openai"})
        assert updated.field("voice") == "charlie"

    def test_inserts_above_the_real_fence_not_a_rule_in_the_prose(self) -> None:
        """The insert lands in the frontmatter, leaving the body untouched."""
        updated = FrontmatterBlock(_FENCED_WITH_PROSE).with_fields(
            {"provider": "openai"}
        )
        assert updated.text == _FENCED_WITH_PROSE.replace(
            'notify: ""\n---\n', 'notify: ""\nprovider: "openai"\n---\n'
        )

    def test_replaces_a_field_without_disturbing_the_prose(self) -> None:
        updated = FrontmatterBlock(_FENCED_WITH_PROSE).with_fields({"voice": "roger"})
        assert updated.field("voice") == "roger"
        assert updated.text.endswith("\nbelow it\n")

    def test_only_edits_fields_above_the_closing_fence(self) -> None:
        block = FrontmatterBlock('---\nnotify: "n"\n---\n' + _BODY_FIELDS)
        updated = block.with_fields({"notify": "c", "voice": "charlie"})
        assert (
            updated.text == '---\nnotify: "c"\nvoice: "charlie"\n---\n' + _BODY_FIELDS
        )

    def test_raw_crlf_block_preserves_its_separators_and_body(self) -> None:
        body = '# Notes\r\nvoice: "body"\r\n'
        block = FrontmatterBlock('---\r\nvoice:\r\n  "fin"\r\n---\r\n' + body)
        assert block.fields() == {"voice": "fin"}
        assert block.field("voice") == "fin"
        updated = block.with_fields({"voice": "charlie", "notify": "c"})
        assert (
            updated.text == '---\r\nvoice: "charlie"\r\nnotify: "c"\r\n---\r\n' + body
        )

    def test_leaves_the_original_untouched(self) -> None:
        """Immutable: the edit is a new block, so a failure partway through a
        batch cannot leave a half-applied one behind."""
        held = FrontmatterBlock(_FENCED)
        held.with_fields({"voice": "roger"})
        assert held.field("voice") == "charlie"

    @pytest.mark.parametrize("value", [r"abc\1", r"abc\q", r"C:\new", r"\g<0>", "a\\b"])
    def test_replaces_a_field_with_its_value_taken_literally(self, value: str) -> None:
        """A backslash is text, not a regex group reference or escape."""
        updated = FrontmatterBlock(_FENCED).with_fields({"voice": value})
        assert updated.field("voice") == value
        assert updated.text == _FENCED.replace('"charlie"', f'"{value}"')


class TestValidateValue:
    @pytest.mark.parametrize("bad", ["a\nb", "a\rb"])
    def test_rejects_newlines(self, bad: str) -> None:
        with pytest.raises(ConfigValueError, match="must not contain newlines"):
            FrontmatterBlock.validate_value(bad)

    @pytest.mark.parametrize("bad", ['I"m tired', 'say "hi"', '"'])
    def test_rejects_double_quotes(self, bad: str) -> None:
        with pytest.raises(ConfigValueError, match="must not contain double-quotes"):
            FrontmatterBlock.validate_value(bad)

    def test_accepts_an_apostrophe(self) -> None:
        FrontmatterBlock.validate_value("I'm tired")  # no raise
