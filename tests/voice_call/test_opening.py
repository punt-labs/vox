"""Tests for the session options built from one worker snapshot."""

from __future__ import annotations

from punt_vox.voxd.voice_call.greeting import CallGreeting
from punt_vox.voxd.voice_call.opening import CallOpening
from punt_vox.voxd.voice_call.seed import CallSeed

from .fakes import payload


def test_opening_speaks_the_greeting_alone_when_nothing_happened_away() -> None:
    options = CallOpening(("/repo",), []).options(("tool",), {}, "")

    assert options.first_message == CallGreeting(("/repo",)).spoken()
    assert options.tool_ids == ("tool",)
    assert options.prompt == CallSeed(("/repo",), []).prompt()


def test_opening_puts_the_greeting_before_the_unchanged_away_text() -> None:
    worker = payload("worker")
    labels = {"The worker in repo": "worker"}
    away = "While you were away, the worker in repo finished: Done."

    options = CallOpening(("/repo",), [worker]).options((), labels, away)

    assert options.first_message == f"{CallGreeting(('/repo',)).spoken()} {away}"
    assert options.prompt == CallSeed(("/repo",), [worker]).prompt(labels)


def test_opening_keeps_the_prompt_within_the_seed_limit() -> None:
    workers = [payload(f"w{i:03d}", "t" * 380, goal="g" * 380) for i in range(40)]
    options = CallOpening(("/a", "/b"), workers).options((), {}, "")
    assert len(options.prompt.encode()) <= CallSeed.LIMIT
