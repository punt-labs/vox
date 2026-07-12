"""Tests for the active-source context holder and its two backing shapes."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.types_programs.prompts import PromptSet
from punt_vox.voxd.programs.active_context import (
    ActiveContext,
    ActiveProgram,
    ActiveSelection,
)
from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.album_tags import AlbumTags
from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.selection import Selection

from .conftest import InMemoryPartStore, make_manifest

_PROMPTS = PromptSet(base="pad", variations=("a", "b"))


def _active(directory: str = "techno--ambient-a3f1c9") -> ActiveProgram:
    manifest = make_manifest(1, 2)
    return ActiveProgram(
        album_id=AlbumId("a3f1c9"),
        store=InMemoryPartStore(manifest),
        tags=AlbumTags(style="techno", vibe="ambient"),
        directory=Path("/music") / directory,
        prompts=_PROMPTS,
    )


def _selection() -> Selection:
    return Selection.from_albums(
        [
            ("album-a", (Part("001.mp3", 1), Part("002.mp3", 2))),
            ("album-b", (Part("001.mp3", 1),)),
        ]
    )


class TestActiveProgram:
    def test_to_plan_carries_store_tags_and_prompts(self) -> None:
        active = _active()
        plan = active.to_plan()
        assert plan.store is active.store
        assert plan.tags == active.tags
        assert plan.prompts == _PROMPTS

    def test_locate_joins_the_single_directory(self) -> None:
        active = _active()
        assert active.locate(Part("001.mp3", 1)) == active.directory / "001.mp3"

    def test_spec_for_composes_prompt_from_base_and_variation(self) -> None:
        plan = _active().to_plan()
        assert plan.spec_for(1).prompt == "pad a"
        assert plan.spec_for(2).prompt == "pad b"
        assert plan.spec_for(3).prompt == "pad a"  # cycles


class TestActiveSelection:
    def test_locate_resolves_each_part_under_root(self) -> None:
        selection = _selection()
        active = ActiveSelection(Path("/music"), selection, "radio")
        first = selection.parts[0]
        assert active.locate(first.playable) == Path("/music/album-a/001.mp3")

    def test_colliding_filenames_resolve_to_distinct_paths(self) -> None:
        selection = _selection()
        active = ActiveSelection(Path("/music"), selection, "radio")
        a_first = selection.parts[0].playable  # album-a/001.mp3
        b_first = selection.parts[2].playable  # album-b/001.mp3
        assert active.locate(a_first) != active.locate(b_first)


class TestActiveContext:
    def test_idle_context_has_no_current(self) -> None:
        ctx = ActiveContext()
        assert ctx.current is None
        assert ctx.name() is None

    def test_plan_raises_while_idle(self) -> None:
        with pytest.raises(RuntimeError, match="no active source"):
            ActiveContext().plan()

    def test_locate_raises_while_idle(self) -> None:
        with pytest.raises(RuntimeError, match="no active source"):
            ActiveContext().locate(Part("001.mp3", 1))

    def test_switch_to_program_activates_it(self) -> None:
        ctx = ActiveContext()
        active = _active()
        ctx.switch(active)
        assert ctx.current is active
        assert ctx.plan().store is active.store
        assert ctx.locate(Part("001.mp3", 1)) == active.directory / "001.mp3"

    def test_switch_to_selection_has_no_plan(self) -> None:
        ctx = ActiveContext()
        ctx.switch(ActiveSelection(Path("/music"), _selection(), "radio"))
        with pytest.raises(RuntimeError, match="consume-only selection"):
            ctx.plan()

    def test_clear_returns_to_idle(self) -> None:
        ctx = ActiveContext()
        ctx.switch(_active())
        ctx.clear()
        assert ctx.current is None

    def test_switch_replaces_the_active_source(self) -> None:
        ctx = ActiveContext()
        ctx.switch(_active("first-a3f1c9"))
        ctx.switch(_active("second-7b2e04"))
        assert ctx.locate(Part("001.mp3", 1)) == Path("/music/second-7b2e04/001.mp3")
