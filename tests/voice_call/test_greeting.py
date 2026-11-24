from __future__ import annotations

from punt_vox.voxd.voice_call.greeting import CallGreeting

_INTRO = "Hello, this is Vox. I start, steer, check and stop coding workers"


def test_greeting_names_the_one_allowlisted_repository_by_its_last_segment() -> None:
    assert CallGreeting(("/home/me/src/repo",)).spoken() == f"{_INTRO} in repo."


def test_greeting_names_two_repositories_in_one_sentence() -> None:
    greeting = CallGreeting(("/a/alpha", "/b/beta")).spoken()
    assert greeting == f"{_INTRO} in alpha and beta."


def test_greeting_without_repositories_gives_the_allow_command() -> None:
    assert CallGreeting(()).spoken() == (
        f"{_INTRO}, but no repository is allowlisted yet, "
        "so run vox worker allow followed by the repository path to add one."
    )


def test_greeting_is_two_spoken_sentences_without_paths_or_markup() -> None:
    greeting = CallGreeting(("/a/alpha", "/b/beta", "/c/gamma")).spoken()
    assert greeting.count(". ") == 1
    assert greeting.endswith(".")
    assert "/" not in greeting
    assert not any(mark in greeting for mark in ("*", "`", "#", "<", ">", "\n", "- "))
    assert "alpha, beta and gamma" in greeting


def test_greeting_qualifies_repositories_that_share_a_name() -> None:
    greeting = CallGreeting(("/work/api", "/home/api", "/x/web")).spoken()
    assert greeting == f"{_INTRO} in work api, home api and web."


def test_empty_allowlist_greeting_has_no_angle_brackets() -> None:
    greeting = CallGreeting(()).spoken()
    assert "<" not in greeting
    assert ">" not in greeting
    assert "vox worker allow" in greeting


def test_greeting_adds_parents_until_shared_names_are_unique() -> None:
    greeting = CallGreeting(("/one/work/api", "/two/work/api")).spoken()
    assert greeting == f"{_INTRO} in one work api and two work api."


def test_greeting_qualifies_with_one_parent_when_that_is_enough() -> None:
    greeting = CallGreeting(("/a/api", "/b/api")).spoken()
    assert greeting == f"{_INTRO} in a api and b api."


def test_greeting_resolves_a_three_way_collision() -> None:
    greeting = CallGreeting(("/x/work/api", "/y/work/api", "/z/api")).spoken()
    assert greeting == f"{_INTRO} in x work api, y work api and z api."
