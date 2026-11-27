"""The caller's scripted turns for ``verify_call`` and how long they take to play."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

__all__ = [
    "PCM_BYTES_PER_S",
    "WORKER_STATUS_TURN",
    "Turn",
    "launch_timeout_s",
    "script_plan",
]

# ``vox call --script`` never returns on its own once the script ends: it idles
# on pings until ``timeout`` kills it, so exit 124 is the normal end of a call.
# The timeout is therefore the script itself, its generated audio plus its
# pauses, and a short drain, not a margin.
PCM_BYTES_PER_S: Final = 32000  # 16 kHz mono s16le.
_DRAIN_S: Final = 45  # For the agent's last reply and any worker stop in flight.
_LEAD_IN_S: Final = 6  # Silence before the caller speaks, so the greeting is whole.
_DOCS_TURN: Final = (
    "What is the Python SDK repository for the Model Context Protocol? "
    "Look it up in the docs."
)
_SEARCH_TURN: Final = (
    "Please search the web for the latest open source language models."
)
_SILENCE_TURN: Final = "Give me a minute, I need to think."
_WORKER_START_TURN: Final = (
    "Please start a coding worker in this repository. Its task is to run "
    "git status and report the result. It must not change any file."
)
WORKER_STATUS_TURN: Final = "Tell me what the worker is doing right now."
_WORKER_GO_TURN: Final = (
    "Yes."  # The gate accepts only a bare yes from its closed list.
)
_WORKER_STOP_TURN: Final = (
    "Please stop the git status worker you just started, not any older worker."
)
_WORKER_CONFIRM_TURN: Final = "Yes."


@dataclass(frozen=True, slots=True)
class Turn:
    """One scripted caller turn: what is said ("" is silence) and the pause after."""

    text: str
    pause_s: int


def script_plan(*, search: bool) -> list[Turn]:
    """Return the caller's turns: lead-in, docs, search if set up, worker, silence."""
    plan = [Turn("", _LEAD_IN_S), Turn(_DOCS_TURN, 45)]
    if search:
        plan.append(Turn(_SEARCH_TURN, 30))
    plan += [
        Turn(_WORKER_START_TURN, 25),
        Turn(_WORKER_GO_TURN, 50),
        Turn(WORKER_STATUS_TURN, 40),
        Turn(_WORKER_STOP_TURN, 30),
        Turn(_WORKER_CONFIRM_TURN, 25),
        Turn(_SILENCE_TURN, 40),
    ]
    return plan


def launch_timeout_s(*, search: bool, audio_s: float) -> int:
    """Return when ``vox call`` is cut off: its audio, its pauses and a short drain.

    ``audio_s`` is how long the generated turns play; the pacer plays each
    turn's audio and then waits its pause.
    """
    pauses = sum(turn.pause_s for turn in script_plan(search=search) if turn.text)
    return math.ceil(audio_s + pauses + _DRAIN_S)
