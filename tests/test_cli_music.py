"""Tests for the consume-only ``vox music`` CLI (cli_music.MusicCli).

MusicCli is a humble object: each command method is driven directly with an
in-memory FakeProgramGateway and a mock formatter -- no daemon, no store -- so
the surface behaviour (album list via the gateway, tag/id replay, next/status,
and the F7 applied/rejected result) is asserted without a wire. A couple of
CliRunner smoke tests confirm build_music_app wires the Typer group.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer
from _program_fakes import FakeProgramGateway
from typer.testing import CliRunner
from websockets.exceptions import WebSocketException

from punt_vox.cli_music import MusicCli, build_music_app
from punt_vox.output_formatter import OutputFormatter
from punt_vox.types_programs import Reason
from punt_vox.types_programs.control import ProgramSummary
from punt_vox.types_programs.identifiers import ProgramName
from punt_vox.types_programs.status import ProgramStatus
from punt_vox.voxd.programs import Part, Program, ProgramState
from punt_vox.voxd.programs.playback_policy import Advance, AdvanceResult


class _AvoidRepeat:
    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        for part in pool:
            if part != playing:
                return Advance(part)
        return Advance(pool[0])


def _cli(gateway: FakeProgramGateway) -> tuple[MusicCli, MagicMock]:
    formatter = MagicMock(spec=OutputFormatter)
    return MusicCli(formatter, lambda: gateway), formatter


def _summary(album_id: str, style: str, vibe: str, ready: int) -> ProgramSummary:
    return ProgramSummary(
        id=album_id, style=style, vibe=vibe, format="music", ready=ready, total=ready
    )


def _emitted(formatter: MagicMock) -> tuple[object, str]:
    payload, text = formatter.emit.call_args.args
    return payload, text


# ---------------------------------------------------------------------------
# list -- albums via the gateway catalog (no client-side store, R2)
# ---------------------------------------------------------------------------


def test_list_renders_albums_from_the_gateway() -> None:
    catalog = (
        _summary("a3f1c9", "trance", "calm", 5),
        _summary("7b2e04", "lofi", "focus", 1),
    )
    cli, formatter = _cli(FakeProgramGateway(catalog=catalog))

    cli.list_programs()

    payload, text = _emitted(formatter)
    ids = [p["id"] for p in payload["programs"]]  # type: ignore[index]
    assert ids == ["a3f1c9", "7b2e04"]
    assert "a3f1c9" in text


def test_list_empty() -> None:
    cli, formatter = _cli(FakeProgramGateway())

    cli.list_programs()

    payload, text = _emitted(formatter)
    assert payload == {"programs": []}
    assert "No saved albums" in text


# ---------------------------------------------------------------------------
# play -- a Selection resolved by tags or id, and the F7 result
# ---------------------------------------------------------------------------


def test_play_by_tags_forwards_the_query() -> None:
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.play("trance", "calm")

    assert fake.calls[0].verb == "select"
    assert fake.calls[0].selection is not None
    assert fake.calls[0].selection.style == "trance"
    assert fake.calls[0].selection.vibe == "calm"


def test_play_by_id_forwards_the_album_id() -> None:
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.play(album_id="a3f1c9")

    assert fake.calls[0].selection is not None
    assert fake.calls[0].selection.id == "a3f1c9"


def test_play_reports_rejected() -> None:
    fake = FakeProgramGateway(applied=False)
    cli, formatter = _cli(fake)

    cli.play("trance")

    payload, _ = _emitted(formatter)
    assert payload["applied"] is False  # type: ignore[index]


def test_play_websocket_error_is_clean_error() -> None:
    """A mid-request WebSocket close on play is a clean CLI error, not raw."""
    gateway = MagicMock()
    gateway.select.side_effect = WebSocketException("connection closed")
    cli = MusicCli(MagicMock(spec=OutputFormatter), lambda: gateway)

    with pytest.raises(typer.Exit):
        cli.play("trance")


def test_status_websocket_handshake_error_is_clean_error() -> None:
    """A stale-token handshake failure on status surfaces cleanly, not raw."""
    gateway = MagicMock()
    gateway.status.side_effect = WebSocketException("invalid status 401")
    cli = MusicCli(MagicMock(spec=OutputFormatter), lambda: gateway)

    with pytest.raises(typer.Exit):
        cli.status()


# ---------------------------------------------------------------------------
# next / status
# ---------------------------------------------------------------------------


def test_next_advances() -> None:
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.advance()

    assert fake.verbs() == ["advance"]


def test_next_rejected_surfaces_reason() -> None:
    """A rejected advance shows the daemon's reason, not a canned line (F4/F7)."""
    fake = FakeProgramGateway(applied=False, reason="nothing is playing")
    cli, formatter = _cli(fake)

    cli.advance()

    payload, text = _emitted(formatter)
    assert payload["applied"] is False  # type: ignore[index]
    assert text == "nothing is playing"


def test_status_renders_now_playing_and_failures() -> None:
    program = Program(ProgramState.initial(), _AvoidRepeat())
    program.turn_on()
    program.first_track_ok(Part("id001", 1))
    program.fill_bad_part(Part("id002", 2), Reason("ToS"))
    status = program.to_status(ProgramName("ambient_techno"))
    cli, formatter = _cli(FakeProgramGateway(status=status))

    cli.status()

    _, text = _emitted(formatter)
    assert "ambient_techno" in text
    assert "playing 1 of 1" in text
    assert "part 2 failed" in text


def test_status_idle() -> None:
    cli, formatter = _cli(FakeProgramGateway(status=ProgramStatus.idle()))

    cli.status()

    _, text = _emitted(formatter)
    assert text == "Nothing playing."


# ---------------------------------------------------------------------------
# build_music_app wiring (CliRunner smoke)
# ---------------------------------------------------------------------------


def test_app_no_subcommand_shows_help() -> None:
    app = build_music_app(OutputFormatter())

    result = CliRunner().invoke(app, [])

    assert result.exit_code != 0 or "Usage" in result.output
