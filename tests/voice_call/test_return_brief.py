from __future__ import annotations

from typing import TYPE_CHECKING

from punt_vox.voxd.voice_call.greeting import CallGreeting
from punt_vox.voxd.voice_call.return_brief import ReturnBrief

from .fakes import payload

if TYPE_CHECKING:
    from pathlib import Path

_GREETING = CallGreeting(("/repo",)).spoken()


def test_return_result_is_not_repeated_once_saved_at_departure(tmp_path: Path) -> None:
    cursor = tmp_path / "departure.json"
    cursor.write_text('{"worker": 1}')
    worker = payload(
        "worker", last_assistant_message="Done.", worker_seq=5, result_seq=3
    )
    brief = ReturnBrief(cursor)

    assert brief.options([worker], ("/repo",), ()).first_message == (
        f"{_GREETING} While you were away, the worker in repo finished: Done."
    )
    assert brief.covered == {"worker": 5}
    brief.depart({})

    assert (
        ReturnBrief(cursor).options([worker], ("/repo",), ()).first_message == _GREETING
    )


def test_fresh_call_opens_with_the_greeting_when_nothing_happened_away(
    tmp_path: Path,
) -> None:
    brief = ReturnBrief(tmp_path / "departure.json")
    options = brief.options([payload("worker")], ("/repo",), ())

    assert options.first_message == _GREETING


def test_fresh_call_without_repositories_greets_with_the_allow_command(
    tmp_path: Path,
) -> None:
    options = ReturnBrief(tmp_path / "departure.json").options([], (), ())

    assert options.first_message == CallGreeting(()).spoken()
    assert options.first_message is not None
    assert "vox worker allow <path>" in options.first_message


def test_greeting_precedes_unchanged_away_updates(tmp_path: Path) -> None:
    brief = ReturnBrief(tmp_path / "departure.json")
    brief.depart({"worker": 0})
    worker = payload("worker", last_assistant_message="Done.", result_seq=1)

    options = brief.options([worker], ("/a/alpha", "/b/beta"), ())

    away = "While you were away, the worker in repo finished: Done."
    assert options.first_message == (
        f"{CallGreeting(('/a/alpha', '/b/beta')).spoken()} {away}"
    )
