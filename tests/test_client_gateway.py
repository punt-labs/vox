"""Tests for the gateway layer: control DTOs and the client-backed adapter.

The DTOs (:mod:`punt_vox.program_control`) are pure value objects; the adapter
(:class:`ClientProgramGateway`) is the wire translation, tested against a mocked
``VoxClientSync`` so no daemon is needed. Both failure surfaces of the F7 result
(applied vs rejected) are exercised.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from punt_vox.client_gateway import ClientProgramGateway
from punt_vox.program_control import CommandOutcome, ProgramSummary, StartRequest
from punt_vox.voxd.programs import (
    Format,
    Mode,
    Part,
    Program,
    ProgramState,
    ProgramStatus,
)
from punt_vox.voxd.programs.identifiers import PartRef, ProgramName
from punt_vox.voxd.programs.playback_policy import Advance, AdvanceResult


class _AvoidRepeat:
    """Anti-repeat policy stand-in for building a playing Program in tests."""

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        for part in pool:
            if part != playing:
                return Advance(part)
        return Advance(pool[0])


def _build_rotating() -> Program:
    """Drive a fresh Program to playing_rotating with a full 12-Part pool."""
    program = Program(ProgramState.initial(), _AvoidRepeat())
    program.turn_on()
    program.first_track_ok(Part("id001", 1))
    for index in range(2, Format.PLAYLIST.pool_size + 1):
        program.fill_ok(Part(f"id{index:03d}", index))
    return program


# ---------------------------------------------------------------------------
# Control DTOs
# ---------------------------------------------------------------------------


def test_command_outcome_applied_and_rejected() -> None:
    """ok/rejected build the two F7 results with the right applied flag."""
    assert CommandOutcome.ok("on") == CommandOutcome(applied=True, message="on")
    assert CommandOutcome.rejected("lost race").applied is False
    assert CommandOutcome.ok("x").to_dict() == {"applied": True, "message": "x"}


def test_start_request_defaults_to_fallback() -> None:
    """An empty StartRequest carries no authoring -- the pool will fall back."""
    request = StartRequest()

    assert request.style is None
    assert request.name is None
    assert request.prompts is None


def test_program_summary_display_line() -> None:
    """A summary renders name, ready/total counts, and the format label."""
    summary = ProgramSummary(name="ambient_techno", format="music", ready=5, total=12)

    line = summary.display_line()

    assert "ambient_techno" in line
    assert "5/12" in line
    assert "music" in line


# ---------------------------------------------------------------------------
# ClientProgramGateway -- wire translation
# ---------------------------------------------------------------------------


def _status_dict(program: Program, name: str) -> dict[str, Any]:
    return ProgramStatus.of(program, ProgramName(name)).to_dict()


def test_status_parses_the_daemon_reply() -> None:
    """status() returns the ProgramStatus the daemon reported, parsed from wire."""
    program = _build_rotating()
    client = MagicMock()
    client.program_status.return_value = {
        "status": _status_dict(program, "ambient_techno")
    }

    status = ClientProgramGateway(client).status()

    assert status.mode is Mode.PLAYING_ROTATING
    assert status.name == ProgramName("ambient_techno")
    assert status.now_playing is not None


def test_start_forwards_request_and_reads_applied() -> None:
    """start() forwards the authored request and reads the F7 applied result."""
    client = MagicMock()
    client.program_on.return_value = {"applied": True, "message": "on"}

    outcome = ClientProgramGateway(client).start(StartRequest(style="techno"))

    client.program_on.assert_called_once_with(style="techno", name=None, prompts=None)
    assert outcome == CommandOutcome(applied=True, message="on")


def test_rejected_command_surfaces_as_not_applied() -> None:
    """A daemon reply of applied=false becomes a rejected CommandOutcome (F7)."""
    client = MagicMock()
    client.program_next.return_value = {"applied": False, "message": "lost race"}

    outcome = ClientProgramGateway(client).advance()

    assert outcome.applied is False
    assert outcome.message == "lost race"


def test_missing_applied_defaults_to_applied() -> None:
    """A reply omitting 'applied' is treated as applied (absence == went through)."""
    client = MagicMock()
    client.program_off.return_value = {}

    outcome = ClientProgramGateway(client).stop()

    assert outcome.applied is True


def test_rejection_without_message_gets_a_non_empty_reason() -> None:
    """A rejected reply with no message never surfaces a blank line (F4/F7)."""
    client = MagicMock()
    client.program_next.return_value = {"applied": False}

    outcome = ClientProgramGateway(client).advance()

    assert outcome.applied is False
    assert outcome.message  # non-empty: the surfaces must have something to show


def test_command_outcome_display_prefers_reason_over_default() -> None:
    """display() shows the daemon reason on rejection, the default only when silent."""
    assert CommandOutcome.rejected("no track").display("Playing.") == "no track"
    assert CommandOutcome.ok("").display("Playing.") == "Playing."
    assert CommandOutcome.ok("live line").display("Playing.") == "live line"
    assert CommandOutcome(applied=False, message="").display("x") == "command rejected"


def test_play_forwards_part_index() -> None:
    """play() forwards the resolved 1-based part index to the wire."""
    client = MagicMock()
    client.program_play.return_value = {"applied": True, "message": "ok"}

    ClientProgramGateway(client).play(ProgramName("saved"), PartRef(Format.PLAYLIST, 2))

    client.program_play.assert_called_once_with("saved", part=2)


def test_play_without_part_sends_none() -> None:
    """play() with no PartRef sends part=None (whole-Program playback)."""
    client = MagicMock()
    client.program_play.return_value = {"applied": True, "message": "ok"}

    ClientProgramGateway(client).play(ProgramName("saved"), None)

    client.program_play.assert_called_once_with("saved", part=None)


def test_loop_forwards_name() -> None:
    """loop() forwards the Program name to the wire."""
    client = MagicMock()
    client.program_loop.return_value = {"applied": True, "message": "ok"}

    ClientProgramGateway(client).loop(ProgramName("saved"))

    client.program_loop.assert_called_once_with("saved")


def test_catalog_parses_summaries() -> None:
    """catalog() parses the program list into typed summaries."""
    client = MagicMock()
    client.program_list.return_value = {
        "programs": [
            {"name": "ambient_techno", "format": "music", "ready": 5, "total": 12},
            {"name": "jazz", "format": "music", "ready": 1},
        ]
    }

    catalog = ClientProgramGateway(client).catalog()

    assert catalog == (
        ProgramSummary(name="ambient_techno", format="music", ready=5, total=12),
        ProgramSummary(name="jazz", format="music", ready=1, total=0),
    )


def test_idle_status_round_trips_through_the_adapter() -> None:
    """An idle daemon reply parses back into the idle status (no active Program)."""
    program = Program(ProgramState.initial(), _AvoidRepeat())
    program.turn_on()
    program.first_track_ok(Part("id001", 1))
    client = MagicMock()
    client.program_status.return_value = {"status": ProgramStatus.idle().to_dict()}

    status = ClientProgramGateway(client).status()

    assert status.is_idle
