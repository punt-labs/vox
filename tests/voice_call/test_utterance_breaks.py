from __future__ import annotations

from punt_vox.voxd.voice_call.utterance_breaks import UtteranceBreaks


def test_the_first_chunk_and_a_new_turn_begin_an_utterance() -> None:
    breaks = UtteranceBreaks()
    assert [breaks.begins(turn) for turn in (1, 1, 1, 2, 2)] == [
        True,
        False,
        False,
        True,
        False,
    ]


def test_a_tool_call_makes_the_next_chunk_begin_one_in_the_same_turn() -> None:
    breaks = UtteranceBreaks()
    breaks.begins(79)
    breaks.note_tool_call()
    assert [breaks.begins(79) for _ in range(3)] == [True, False, False]
