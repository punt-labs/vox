"""Tests for the active-Program context holder and its immutable value object."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.music_prompts import PromptSet
from punt_vox.voxd.programs.active_context import ActiveContext, ActiveProgram
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import PlaylistSubject

from .conftest import InMemoryPartStore, make_manifest

_PROMPTS = PromptSet(base="pad", variations=("a", "b"))


def _active(name: str = "ambient_techno") -> ActiveProgram:
    store = InMemoryPartStore(make_manifest(name, 1, 2))
    return ActiveProgram(
        name=ProgramName(name),
        store=store,
        subject=PlaylistSubject(vibe="ambient", style="techno"),
        directory=Path("/music") / name,
        prompts=_PROMPTS,
    )


class TestActiveProgram:
    def test_to_plan_carries_store_subject_and_prompts(self) -> None:
        active = _active()
        plan = active.to_plan()
        assert plan.store is active.store
        assert plan.subject == active.subject
        assert plan.prompts == _PROMPTS

    def test_spec_for_composes_prompt_from_base_and_variation(self) -> None:
        plan = _active().to_plan()
        assert plan.spec_for(1).prompt == "pad a"
        assert plan.spec_for(2).prompt == "pad b"
        assert plan.spec_for(3).prompt == "pad a"  # cycles

    def test_spec_for_titles_the_part_from_the_raw_variation(self) -> None:
        plan = _active().to_plan()
        assert plan.spec_for(1).tags.title == "a"
        assert plan.spec_for(2).tags.title == "b"
        assert plan.spec_for(3).tags.title == "a"  # cycles


class TestActiveContext:
    def test_idle_context_has_no_current(self) -> None:
        assert ActiveContext().current is None

    def test_plan_raises_while_idle(self) -> None:
        with pytest.raises(RuntimeError, match="no active Program"):
            ActiveContext().plan()

    def test_directory_raises_while_idle(self) -> None:
        with pytest.raises(RuntimeError, match="no active Program"):
            ActiveContext().directory()

    def test_switch_makes_a_program_active(self) -> None:
        ctx = ActiveContext()
        active = _active()
        ctx.switch(active)
        assert ctx.current is active
        assert ctx.directory() == Path("/music/ambient_techno")
        assert ctx.plan().store is active.store

    def test_switch_replaces_the_active_program(self) -> None:
        ctx = ActiveContext()
        ctx.switch(_active("first"))
        ctx.switch(_active("second"))
        assert ctx.directory() == Path("/music/second")
