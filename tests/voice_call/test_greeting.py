"""Tests for the spoken opener: a conversational hello, not a product pitch."""

from __future__ import annotations

from punt_vox.voxd.voice_call.greeting import CallGreeting

_HELLO = "Hi, it's Vox. What are we working on?"


def test_greeting_is_a_conversational_hello() -> None:
    assert CallGreeting(("/home/me/src/repo",)).spoken() == _HELLO


def test_greeting_does_not_recite_the_workers_job() -> None:
    greeting = CallGreeting(("/a/alpha", "/b/beta")).spoken()
    assert "start, steer" not in greeting
    assert "coding workers" not in greeting


def test_greeting_is_the_same_for_any_allowlist_with_a_repository() -> None:
    assert CallGreeting(("/a/alpha", "/b/beta")).spoken() == _HELLO


def test_greeting_without_repositories_adds_how_to_allowlist_one() -> None:
    assert CallGreeting(()).spoken() == (
        f"{_HELLO} To have me start coding workers, run vox worker allow "
        "followed by the repository path."
    )


def test_greeting_is_spoken_text_without_paths_or_markup() -> None:
    for projects in ((), ("/a/alpha",)):
        greeting = CallGreeting(projects).spoken()
        assert "/" not in greeting
        assert not any(mark in greeting for mark in ("*", "`", "#", "<", ">", "\n"))
