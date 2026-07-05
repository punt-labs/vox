"""Tests for punt_vox.vibe -- VibeChange resolution rules."""

from __future__ import annotations

import pytest

from punt_vox.vibe import VALID_VIBE_MODES, VibeChange


class TestVibeChangeResolve:
    """VibeChange.resolve maps a request to authoritative config updates."""

    def test_manual_mood_and_tags(self) -> None:
        updates = VibeChange(mood="excited", tags="[excited]", mode="manual").resolve()
        assert updates == {
            "vibe": "excited",
            "vibe_tags": "[excited]",
            "vibe_signals": "",
            "vibe_mode": "manual",
        }

    def test_mood_only(self) -> None:
        assert VibeChange(mood="calm", tags=None, mode=None).resolve() == {
            "vibe": "calm"
        }

    def test_tags_only_clears_signals(self) -> None:
        updates = VibeChange(mood=None, tags="[warm]", mode=None).resolve()
        assert updates == {"vibe_tags": "[warm]", "vibe_signals": ""}

    def test_auto_resets_whole_cluster(self) -> None:
        # /vibe auto: mood + tags + signals cleared, mode set (vox-73m5).
        updates = VibeChange(mood=None, tags="", mode="auto").resolve()
        assert updates == {
            "vibe": "",
            "vibe_tags": "",
            "vibe_signals": "",
            "vibe_mode": "auto",
        }

    def test_auto_ignores_supplied_mood(self) -> None:
        # A mood passed alongside auto is dropped -- auto means automatic.
        updates = VibeChange(mood="sad", tags=None, mode="auto").resolve()
        assert updates["vibe"] == ""
        assert updates["vibe_mode"] == "auto"

    def test_off_resets_whole_cluster(self) -> None:
        updates = VibeChange(mood=None, tags="", mode="off").resolve()
        assert updates["vibe"] == ""
        assert updates["vibe_tags"] == ""
        assert updates["vibe_mode"] == "off"

    def test_empty_change_yields_no_updates(self) -> None:
        assert VibeChange(mood=None, tags=None, mode=None).resolve() == {}

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid vibe mode"):
            VibeChange(mood=None, tags=None, mode="sideways").resolve()

    def test_valid_modes_constant(self) -> None:
        assert frozenset({"auto", "manual", "off"}) == VALID_VIBE_MODES
