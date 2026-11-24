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
        "so run vox worker allow <path> to add one."
    )


def test_greeting_is_two_spoken_sentences_without_paths_or_markup() -> None:
    greeting = CallGreeting(("/a/alpha", "/b/beta", "/c/gamma")).spoken()
    assert greeting.count(". ") == 1
    assert greeting.endswith(".")
    assert "/" not in greeting
    assert not any(mark in greeting for mark in ("*", "`", "#", "\n", "- "))
    assert "alpha, beta and gamma" in greeting
