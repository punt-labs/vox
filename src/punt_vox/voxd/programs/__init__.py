"""The audio Programs domain -- a format-general playlist/podcast/audiobook model.

Phase 1 realises the *playlist* format of ``docs/audio-programs.tex``: a named,
ordered collection of ready :class:`Part` audio that plays, advances, and
rotates for free once generated. The domain is pure -- no I/O, no daemon, no
session -- so every state, transition, and invariant is unit-testable against
the Z model. See ``docs/audio-programs-phase1-design.md`` for the code map.
"""

from __future__ import annotations

from punt_vox.voxd.programs.format import MAX_RETRY, Format
from punt_vox.voxd.programs.identifiers import PartRef, ProgramName, Reason
from punt_vox.voxd.programs.mode import Mode, PlaybackStatus
from punt_vox.voxd.programs.part import FrozenParts, Part, PartStatus
from punt_vox.voxd.programs.playback_policy import (
    COMPLETE,
    Advance,
    AdvanceResult,
    Complete,
    PlaybackPolicy,
)
from punt_vox.voxd.programs.program import Program
from punt_vox.voxd.programs.state import Activation, ProgramState
from punt_vox.voxd.programs.status import ProgramStatus
from punt_vox.voxd.programs.status_views import (
    FailedPartView,
    GenerationStatus,
    NowPlaying,
)

__all__ = [
    "COMPLETE",
    "MAX_RETRY",
    "Activation",
    "Advance",
    "AdvanceResult",
    "Complete",
    "FailedPartView",
    "Format",
    "FrozenParts",
    "GenerationStatus",
    "Mode",
    "NowPlaying",
    "Part",
    "PartRef",
    "PartStatus",
    "PlaybackPolicy",
    "PlaybackStatus",
    "Program",
    "ProgramName",
    "ProgramState",
    "ProgramStatus",
    "Reason",
]
