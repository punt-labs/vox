"""Neutral wire value types the voxd clients return and parse.

These are the format-general, importable-anywhere value objects that cross the
``voxd`` wire: the Program runtime status and its sub-views, the format/mode
enums, the ``ProgramName`` identity, the ``PromptSet`` generation input, the
command request/result DTOs, and the ``JsonObject`` deserialization helper.
They carry no I/O and no state-machine logic, so a thin client (and the daemon)
can name them without importing the Programs domain. ``HealthStatus`` is
re-exported here too as the one non-Program value a client also parses.
"""

from __future__ import annotations

from punt_vox.types_health import HealthStatus
from punt_vox.types_programs.control import (
    CommandOutcome,
    ProgramSummary,
    SelectionRequest,
    StartRequest,
)
from punt_vox.types_programs.format import MAX_RETRY, Format
from punt_vox.types_programs.identifiers import PartRef, ProgramName, Reason
from punt_vox.types_programs.mode import Mode, PlaybackStatus
from punt_vox.types_programs.playback_fault import PlaybackFault, PlaybackFaultKind
from punt_vox.types_programs.prompts import POOL_SIZE, PromptSet
from punt_vox.types_programs.status import ProgramStatus
from punt_vox.types_programs.status_views import (
    FailedPartView,
    GenerationStatus,
    NowPlaying,
)
from punt_vox.types_programs.wire import JsonObject

__all__ = [
    "MAX_RETRY",
    "POOL_SIZE",
    "CommandOutcome",
    "FailedPartView",
    "Format",
    "GenerationStatus",
    "HealthStatus",
    "JsonObject",
    "Mode",
    "NowPlaying",
    "PartRef",
    "PlaybackFault",
    "PlaybackFaultKind",
    "PlaybackStatus",
    "ProgramName",
    "ProgramStatus",
    "ProgramSummary",
    "PromptSet",
    "Reason",
    "SelectionRequest",
    "StartRequest",
]
