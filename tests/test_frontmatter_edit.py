"""Tests for punt_vox.frontmatter_edit -- one edit and what admits it."""

from __future__ import annotations

import errno
import logging
from pathlib import Path

import pytest

from punt_vox.frontmatter_block import FrontmatterBlock
from punt_vox.frontmatter_edit import FrontmatterEdit

_PATH = Path("/config/vox.md")
_FENCED = FrontmatterBlock('---\nvoice: "charlie"\n---\n')
_UNFENCED = FrontmatterBlock('---\nvoice: "charlie"\n')


def _plain(updates: dict[str, str]) -> FrontmatterEdit:
    return FrontmatterEdit(_PATH, lambda _held: updates, None)


def test_a_plain_edit_is_admitted_whatever_the_file_holds() -> None:
    edit = _plain({"voice": "fin"})
    assert not edit.checked
    for held in (None, _FENCED, _UNFENCED):
        edit.admit(held)


def test_a_checked_edit_runs_its_check_against_the_held_fields() -> None:
    seen: list[dict[str, str]] = []
    edit = FrontmatterEdit(_PATH, lambda _held: {"voice": "fin"}, seen.append)
    assert edit.checked
    edit.admit(None)
    edit.admit(_FENCED)
    assert seen == [{}, {"voice": "charlie"}]


def test_a_refused_check_propagates_as_a_value_error() -> None:
    def refuse(_fields: dict[str, str]) -> None:
        raise ValueError("refused")

    with pytest.raises(ValueError, match="refused"):
        FrontmatterEdit(_PATH, lambda _held: {}, refuse).admit(_FENCED)


def test_a_checked_edit_refuses_an_unfenced_file_as_a_format_fault() -> None:
    edit = FrontmatterEdit(_PATH, lambda _held: {"voice": "fin"}, lambda _f: None)
    with pytest.raises(OSError, match="no closing ---") as refused:
        edit.admit(_UNFENCED)
    assert not isinstance(refused.value, ValueError)
    assert refused.value.errno == errno.EBADMSG
    assert refused.value.filename == str(_PATH)


def test_fields_are_computed_from_the_held_block() -> None:
    def bump(held: FrontmatterBlock | None) -> dict[str, str]:
        current = None if held is None else held.field("voice")
        return {"voice": f"{current}-fin"}

    edit = FrontmatterEdit(_PATH, bump, None)
    assert edit.fields(None) == {"voice": "None-fin"}
    assert edit.fields(_FENCED) == {"voice": "charlie-fin"}


def test_block_renders_a_fresh_file_when_there_is_none() -> None:
    block = _plain({}).block(None, {"voice": "fin"})
    assert block.text == '---\nvoice: "fin"\n---\n'


def test_block_edits_a_fenced_file_in_place() -> None:
    block = _plain({}).block(_FENCED, {"voice": "fin"})
    assert block.text == '---\nvoice: "fin"\n---\n'


def test_block_rebuilds_an_unfenced_file_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="punt_vox.frontmatter_edit"):
        block = _plain({}).block(_UNFENCED, {"notify": "y"})
    assert block.text == '---\nnotify: "y"\n---\n'
    assert any(str(_PATH) in r.getMessage() for r in caplog.records)
