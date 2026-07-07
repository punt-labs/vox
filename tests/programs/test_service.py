"""Tests for ``ProgramService`` -- the daemon's handler-facing Program seam.

The service is driven synchronously via ``run_once`` (apply exactly one queued
command), so each handler-facing call and its serialized effect are asserted
without a running event-loop consumer. The Producer is a fake; the store is a
real filesystem store under ``tmp_path`` so replay resolves from disk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.programs import Format, Mode, ProgramName
from punt_vox.voxd.programs.identifiers import PartRef

from .conftest import make_service, seed_program

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.service import ProgramService


def _service(tmp_path: Path) -> ProgramService:
    return make_service(tmp_path / "programs")


def _seed(tmp_path: Path, name: str, *indices: int) -> None:
    seed_program(tmp_path / "programs", name, *indices)


class TestStatus:
    def test_idle_before_any_command(self, tmp_path: Path) -> None:
        assert _service(tmp_path).status().is_idle

    async def test_turn_on_makes_the_program_active(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        service.turn_on(style="techno", name="mix", prompts=None)
        await service.run_once()
        service.shutdown()  # cancel the fill the switch armed before it runs
        status = service.status()
        assert not status.is_idle
        assert status.name == ProgramName("mix")
        assert status.mode is Mode.GENERATING_FIRST

    async def test_turn_on_persists_a_manifest(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        service.turn_on(style="techno", name="mix", prompts=None)
        service.shutdown()
        assert [m.name.value for m in service.saved_programs()] == ["mix"]


class TestReplay:
    async def test_play_cold_starts_from_disk_without_filling(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path, "saved", 1, 2)
        service = _service(tmp_path)
        service.play(ProgramName("saved"), None)
        await service.run_once()
        status = service.status()
        assert status.name == ProgramName("saved")
        assert status.mode is Mode.PLAYING_FILLING
        assert status.generation.filling is False  # replay never generates
        assert status.now_playing is not None
        assert status.now_playing.of == 2

    async def test_play_at_a_part_index(self, tmp_path: Path) -> None:
        _seed(tmp_path, "saved", 1, 2, 3)
        service = _service(tmp_path)
        service.play(ProgramName("saved"), PartRef(Format.PLAYLIST, 2))
        await service.run_once()
        now = service.status().now_playing
        assert now is not None
        assert now.index == 2

    def test_play_unknown_program_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="no saved program"):
            _service(tmp_path).play(ProgramName("ghost"), None)

    def test_play_out_of_range_part_raises(self, tmp_path: Path) -> None:
        _seed(tmp_path, "saved", 1, 2)
        with pytest.raises(ValueError, match="out of range"):
            _service(tmp_path).play(ProgramName("saved"), PartRef(Format.PLAYLIST, 9))

    def test_play_program_with_no_ready_parts_raises(self, tmp_path: Path) -> None:
        _seed(tmp_path, "empty")  # no parts
        with pytest.raises(ValueError, match="no ready parts"):
            _service(tmp_path).play(ProgramName("empty"), None)


class TestConsumeControls:
    async def test_advance_rotates_the_playing_pool(self, tmp_path: Path) -> None:
        _seed(tmp_path, "saved", 1, 2, 3)
        service = _service(tmp_path)
        service.play(ProgramName("saved"), None)
        await service.run_once()
        first = service.status().now_playing
        service.advance()
        await service.run_once()
        assert service.status().now_playing is not None
        assert first is not None

    async def test_off_turns_the_program_off(self, tmp_path: Path) -> None:
        _seed(tmp_path, "saved", 1, 2)
        service = _service(tmp_path)
        service.play(ProgramName("saved"), None)
        await service.run_once()
        service.off()
        await service.run_once()
        status = service.status()
        assert status.mode is Mode.OFF
        assert status.name == ProgramName("saved")  # O1: off keeps the name


class TestNameFallback:
    """Fix #3: a turn-on with no name derives one from style, else the defaults.

    Reachable via ``/music on --style techno`` (OnHandler._opt_str returns None
    when the wire omits ``name``), the live successor to the retired
    ``auto_track_name`` defaults.
    """

    async def test_name_falls_back_to_style_when_name_absent(
        self, tmp_path: Path
    ) -> None:
        service = _service(tmp_path)
        service.turn_on(style="techno", name=None, prompts=None)
        await service.run_once()
        service.shutdown()
        assert service.status().name == ProgramName("techno")

    async def test_name_and_style_default_when_both_absent(
        self, tmp_path: Path
    ) -> None:
        service = _service(tmp_path)
        service.turn_on(style=None, name=None, prompts=None)
        await service.run_once()
        service.shutdown()

        assert service.status().name == ProgramName("music")
        manifest = next(
            m for m in service.saved_programs() if m.name == ProgramName("music")
        )
        assert manifest.subject.style == "ambient"  # _DEFAULT_STYLE

    async def test_blank_name_and_style_fall_through_to_music(
        self, tmp_path: Path
    ) -> None:
        service = _service(tmp_path)
        service.turn_on(style="   ", name="  ", prompts=None)
        await service.run_once()
        service.shutdown()
        assert service.status().name == ProgramName("music")


class TestResume:
    async def test_turn_on_resumes_a_saved_pool_playing(self, tmp_path: Path) -> None:
        _seed(tmp_path, "mix", 1, 2)
        service = _service(tmp_path)
        service.turn_on(style="techno", name="mix", prompts=None)
        await service.run_once()
        service.shutdown()
        status = service.status()
        assert status.mode is Mode.PLAYING_FILLING  # resumed, not regenerated
        assert status.now_playing is not None
