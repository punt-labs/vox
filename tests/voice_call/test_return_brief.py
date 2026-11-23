from __future__ import annotations

from typing import TYPE_CHECKING

from punt_vox.voxd.voice_call.return_brief import ReturnBrief

from .fakes import payload

if TYPE_CHECKING:
    from pathlib import Path


def test_return_result_is_not_repeated_once_saved_at_departure(tmp_path: Path) -> None:
    cursor = tmp_path / "departure.json"
    cursor.write_text('{"worker": 1}')
    worker = payload(
        "worker", last_assistant_message="Done.", worker_seq=5, result_seq=3
    )
    brief = ReturnBrief(cursor)

    assert brief.options([worker], ("/repo",), ()).first_message == (
        "While you were away, the worker in repo finished: Done."
    )
    assert brief.covered == {"worker": 5}
    brief.depart({})

    assert ReturnBrief(cursor).options([worker], ("/repo",), ()).first_message is None
