"""Tests for the consume-only ``vox music`` CLI (cli_music.MusicCli).

MusicCli is a humble object: each command method is driven directly with an
in-memory FakeProgramGateway and a mock formatter -- no daemon, no CliRunner --
so the surface behaviour (grouped list, playlist:N resolution incl. out-of-range,
loop/next/status/migrate, and the F7 applied/rejected result) is asserted
without a wire. A couple of CliRunner smoke tests confirm build_music_app wires
the Typer group.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer
from _program_fakes import FakeProgramGateway
from typer.testing import CliRunner
from websockets.exceptions import WebSocketException

from punt_vox.cli_music import MusicCli, build_music_app
from punt_vox.output_formatter import OutputFormatter
from punt_vox.voxd.programs import Format, Part, Program, ProgramState, Reason
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import PartEntry, PlaylistSubject, ProgramManifest
from punt_vox.voxd.programs.part import PartStatus
from punt_vox.voxd.programs.playback_policy import Advance, AdvanceResult
from punt_vox.voxd.programs.status import ProgramStatus


class _AvoidRepeat:
    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        for part in pool:
            if part != playing:
                return Advance(part)
        return Advance(pool[0])


@pytest.fixture(autouse=True)
def _programs_dir(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the saved-Programs root at an isolated tmp dir.

    Programs live directly under the music root now, so the pool directories
    land in ``tmp_path/<name>/`` with no ``programs/`` segment between them.
    """
    monkeypatch.setenv("VOX_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def _cli(gateway: FakeProgramGateway) -> tuple[MusicCli, MagicMock]:
    formatter = MagicMock(spec=OutputFormatter)
    return MusicCli(formatter, lambda: gateway), formatter


def _save(root: Path, name: str, ready: int, failed: int = 0) -> None:
    parts = [
        PartEntry(
            index=i, file=f"{i:03d}.mp3", status=PartStatus.READY, duration_ms=120
        )
        for i in range(1, ready + 1)
    ]
    parts += [
        PartEntry(
            index=ready + j,
            file=f"{ready + j:03d}.mp3",
            status=PartStatus.FAILED,
            reason="bad",
        )
        for j in range(1, failed + 1)
    ]
    manifest = ProgramManifest(
        name=ProgramName(name),
        fmt=Format.PLAYLIST,
        subject=PlaylistSubject(vibe="ambient", style="techno"),
        parts=tuple(parts),
    )
    FilesystemProgramStore(root).create(manifest)


def _save_gapped(root: Path, name: str) -> None:
    """Save a program whose ready indices are 1, 2, 4 (index 3 permanently failed).

    The gap makes intrinsic index and pool position diverge, so ``playlist:4``
    must resolve while ``playlist:3`` must be rejected (PR #299, MAJOR-1).
    """
    parts = (
        PartEntry(index=1, file="001.mp3", status=PartStatus.READY, duration_ms=120),
        PartEntry(index=2, file="002.mp3", status=PartStatus.READY, duration_ms=120),
        PartEntry(index=3, file="003.mp3", status=PartStatus.FAILED, reason="bad"),
        PartEntry(index=4, file="004.mp3", status=PartStatus.READY, duration_ms=120),
    )
    manifest = ProgramManifest(
        name=ProgramName(name),
        fmt=Format.PLAYLIST,
        subject=PlaylistSubject(vibe="ambient", style="techno"),
        parts=parts,
    )
    FilesystemProgramStore(root).create(manifest)


def _emitted(formatter: MagicMock) -> tuple[object, str]:
    payload, text = formatter.emit.call_args.args
    return payload, text


# ---------------------------------------------------------------------------
# list -- grouped by Program
# ---------------------------------------------------------------------------


def test_list_groups_saved_programs(_programs_dir: Path) -> None:
    _save(_programs_dir, "ambient_techno", ready=5)
    _save(_programs_dir, "jazz", ready=1)
    cli, formatter = _cli(FakeProgramGateway())

    cli.list_programs()

    payload, text = _emitted(formatter)
    names = [p["name"] for p in payload["programs"]]  # type: ignore[index]
    assert names == ["ambient_techno", "jazz"]
    assert "ambient_techno — 5/5 part(s)" in text


def test_list_empty(_programs_dir: Path) -> None:
    cli, formatter = _cli(FakeProgramGateway())

    cli.list_programs()

    payload, text = _emitted(formatter)
    assert payload == {"programs": []}
    assert "No saved programs" in text


# ---------------------------------------------------------------------------
# play -- part resolution and the F7 result
# ---------------------------------------------------------------------------


def test_play_whole_program(_programs_dir: Path) -> None:
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.play("ambient_techno")

    assert fake.calls[0].verb == "play"
    assert fake.calls[0].name == "ambient_techno"
    assert fake.calls[0].part is None


def test_play_resolves_part_index(_programs_dir: Path) -> None:
    _save(_programs_dir, "ambient_techno", ready=3)
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.play("ambient_techno", "playlist:2")

    assert fake.calls[0].part == 2


def test_play_out_of_range_is_clean_error(_programs_dir: Path) -> None:
    _save(_programs_dir, "ambient_techno", ready=3)
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    with pytest.raises(typer.Exit):
        cli.play("ambient_techno", "playlist:5")
    assert fake.calls == []  # rejected before any transition (finding #7)


def test_play_resolves_intrinsic_index_across_gap(_programs_dir: Path) -> None:
    """``playlist:4`` resolves to intrinsic index 4 across a gap, not position 3."""
    _save_gapped(_programs_dir, "gapped")
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.play("gapped", "playlist:4")

    assert fake.calls[0].part == 4  # intrinsic index 4, not the position-3 it holds


def test_play_absent_gap_index_is_clean_error(_programs_dir: Path) -> None:
    """``playlist:3`` (the failed index) is rejected before any transition (#7)."""
    _save_gapped(_programs_dir, "gapped")
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    with pytest.raises(typer.Exit):
        cli.play("gapped", "playlist:3")
    assert fake.calls == []


def test_play_malformed_part_is_clean_error(_programs_dir: Path) -> None:
    _save(_programs_dir, "ambient_techno", ready=3)
    cli, _ = _cli(FakeProgramGateway())

    with pytest.raises(typer.Exit):
        cli.play("ambient_techno", "playlist:x")


def test_play_unknown_program_part_is_clean_error(_programs_dir: Path) -> None:
    cli, _ = _cli(FakeProgramGateway())

    with pytest.raises(typer.Exit):
        cli.play("nope", "playlist:1")


def test_play_reports_rejected(_programs_dir: Path) -> None:
    fake = FakeProgramGateway(applied=False)
    cli, formatter = _cli(fake)

    cli.play("ambient_techno")

    payload, _ = _emitted(formatter)
    assert payload["applied"] is False  # type: ignore[index]


def test_play_websocket_error_is_clean_error(_programs_dir: Path) -> None:
    """A mid-request WebSocket close on play is a clean CLI error, not raw (F1)."""
    gateway = MagicMock()
    gateway.play.side_effect = WebSocketException("connection closed")
    cli = MusicCli(MagicMock(spec=OutputFormatter), lambda: gateway)

    with pytest.raises(typer.Exit):
        cli.play("ambient_techno")


def test_status_websocket_handshake_error_is_clean_error(_programs_dir: Path) -> None:
    """A stale-token handshake failure on status surfaces cleanly, not raw (F1)."""
    gateway = MagicMock()
    gateway.status.side_effect = WebSocketException("invalid status 401")
    cli = MusicCli(MagicMock(spec=OutputFormatter), lambda: gateway)

    with pytest.raises(typer.Exit):
        cli.status()


def test_play_wrong_format_token_is_clean_error(_programs_dir: Path) -> None:
    """A part token whose format differs from the manifest errors before any move."""
    _save(_programs_dir, "ambient_techno", ready=3)
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    with pytest.raises(typer.Exit):
        cli.play("ambient_techno", "podcast:2")
    assert fake.calls == []  # rejected before crossing to the daemon


# ---------------------------------------------------------------------------
# loop / next / status
# ---------------------------------------------------------------------------


def test_loop_forwards_name() -> None:
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.loop("ambient_techno")

    assert fake.calls[0].verb == "loop"
    assert fake.calls[0].name == "ambient_techno"


def test_next_advances() -> None:
    fake = FakeProgramGateway()
    cli, _ = _cli(fake)

    cli.advance()

    assert fake.verbs() == ["advance"]


def test_next_rejected_surfaces_reason(_programs_dir: Path) -> None:
    """A rejected advance shows the daemon's reason, not a canned line (F4/F7)."""
    fake = FakeProgramGateway(applied=False, reason="nothing is playing")
    cli, formatter = _cli(fake)

    cli.advance()

    payload, text = _emitted(formatter)
    assert payload["applied"] is False  # type: ignore[index]
    assert text == "nothing is playing"


def test_play_applied_without_message_uses_default(_programs_dir: Path) -> None:
    """An applied play with no daemon message renders the surface line, not blank."""
    fake = FakeProgramGateway()  # applied, empty daemon message
    cli, formatter = _cli(fake)

    cli.play("ambient_techno")

    _, text = _emitted(formatter)
    assert text == "Playing ambient_techno."


def test_status_renders_now_playing_and_failures() -> None:
    program = Program(ProgramState.initial(), _AvoidRepeat())
    program.turn_on()
    program.first_track_ok(Part("id001", 1))
    program.fill_bad_part(Part("id002", 2), Reason("ToS"))
    status = ProgramStatus.of(program, ProgramName("ambient_techno"))
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


def test_app_lists_via_runner(_programs_dir: Path) -> None:
    _save(_programs_dir, "ambient_techno", ready=2)
    app = build_music_app(OutputFormatter())

    result = CliRunner().invoke(app, ["list"])

    assert result.exit_code == 0
    assert "ambient_techno" in result.output


def test_app_no_subcommand_shows_help() -> None:
    app = build_music_app(OutputFormatter())

    result = CliRunner().invoke(app, [])

    assert result.exit_code != 0 or "Usage" in result.output
