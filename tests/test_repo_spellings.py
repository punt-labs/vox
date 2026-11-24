"""Tests for punt_vox.repo_spellings -- how a disallow names an entry."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.repo_spellings import RepoSpellings


def test_the_resolved_path_is_the_spelling_a_refusal_names() -> None:
    assert RepoSpellings("/repo", "repo").resolved == "/repo"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_spelling_is_refused_by_name(blank: str) -> None:
    with pytest.raises(ValueError, match=f"worker_repos entry blank: {blank!r}"):
        RepoSpellings(blank)
    with pytest.raises(ValueError, match=f"worker_repos entry blank: {blank!r}"):
        RepoSpellings("/repo", blank)


def test_a_stored_entry_matches_by_exact_text() -> None:
    named = RepoSpellings("/elsewhere", "relative/repo")
    assert named.names("relative/repo")
    assert not named.names("relative/other")


def test_a_stored_entry_matches_by_resolved_path(tmp_path: Path) -> None:
    alias = tmp_path / "x" / ".." / "repo"
    assert RepoSpellings(str(tmp_path / "repo")).names(str(alias))
    assert RepoSpellings(str(alias)).names(str(tmp_path / "repo"))


def test_a_relative_spelling_never_matches_by_resolution(tmp_path: Path) -> None:
    """Only an absolute spelling has a resolved form to compare."""
    (tmp_path / "repo").mkdir()
    assert not RepoSpellings("repo").names(str(tmp_path / "repo"))
    assert not RepoSpellings(str(tmp_path / "repo")).names("repo")


def test_a_spelling_equal_to_the_resolved_path_is_not_offered_twice() -> None:
    named = RepoSpellings("/repo", "/repo")
    assert named.names("/repo")
    assert named.resolved == "/repo"


def test_wire_fields_carry_the_spelling_only_when_it_differs() -> None:
    assert RepoSpellings("/repo", "repo").fields() == {
        "project": "/repo",
        "spelling": "repo",
    }
    assert RepoSpellings("/repo", "/repo").fields() == {"project": "/repo"}
    assert RepoSpellings("/repo").fields() == {"project": "/repo"}


def test_a_spelling_that_cannot_expand_matches_by_text_alone() -> None:
    """A ``~user`` spelling for an unknown account has no resolved form.

    It must neither raise nor block the comparison of other stored entries,
    so a hand-edited entry can still be removed by typing it exactly.
    """
    typed = "~no-such-user-for-this-test/repo"
    named = RepoSpellings("/elsewhere", typed)
    assert named.names(typed)
    assert not named.names("/elsewhere/other")
    assert not RepoSpellings("/elsewhere").names(typed)
