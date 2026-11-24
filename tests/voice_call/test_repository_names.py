from __future__ import annotations

from punt_vox.voxd.voice_call.repository_names import RepositoryNames


def test_names_use_the_last_segment_when_unique() -> None:
    assert tuple(RepositoryNames(("/a/alpha", "/b/beta"))) == ("alpha", "beta")


def test_names_add_one_parent_when_that_makes_them_unique() -> None:
    assert tuple(RepositoryNames(("/a/api", "/b/api"))) == ("a api", "b api")


def test_names_add_parents_until_unique() -> None:
    names = RepositoryNames(("/one/work/api", "/two/work/api"))
    assert tuple(names) == ("one work api", "two work api")


def test_names_resolve_a_three_way_collision() -> None:
    names = RepositoryNames(("/x/work/api", "/y/work/api", "/z/api"))
    assert tuple(names) == ("x work api", "y work api", "z api")


def test_names_never_contain_slashes() -> None:
    assert all("/" not in name for name in RepositoryNames(("/a/b/c", "/d/b/c")))


def test_empty_names_are_falsy_and_listed_joins_with_and() -> None:
    assert not RepositoryNames(())
    assert RepositoryNames(("/a/one",)).listed() == "one"
    assert RepositoryNames(("/a/one", "/b/two")).listed() == "one and two"
    assert RepositoryNames(("/a/one", "/b/two", "/c/three")).listed() == (
        "one, two and three"
    )
