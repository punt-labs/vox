"""Guard: vox never asks luxd to raise a frame.

luxd removed the agent-facing raise (agents read the display, only the user
changes it), so a surviving call answers 404 at runtime. Reopening a closed
window is the display's job, done when its menu entry -- registered with the
frame's id -- is clicked.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "punt_vox"
_REMOVED_SPELLINGS = ("frame.raise_", "raise_frame", "FrameRaiser")


def test_no_source_file_asks_luxd_to_raise_a_frame() -> None:
    offenders = sorted(
        f"{path.relative_to(_SRC)}: {spelling}"
        for path in _SRC.rglob("*.py")
        for spelling in _REMOVED_SPELLINGS
        if spelling in path.read_text(encoding="utf-8")
    )
    assert offenders == []
