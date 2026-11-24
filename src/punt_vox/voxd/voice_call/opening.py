"""What the agent knows and says first: the prompt and the opening message."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent.contracts import SessionOptions
from punt_vox.voxd.voice_call.greeting import CallGreeting
from punt_vox.voxd.voice_call.seed import CallSeed

if TYPE_CHECKING:
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["CallOpening"]


@final
class CallOpening:
    """Build one call's session options from one worker snapshot."""

    __slots__ = ("_projects", "_workers")
    _projects: tuple[str, ...]
    _workers: list[WorkerPayload]

    def __new__(cls, projects: tuple[str, ...], workers: list[WorkerPayload]) -> Self:
        """Bind the allowlisted repositories and the registry snapshot."""
        self = super().__new__(cls)
        self._projects = projects
        self._workers = workers
        return self

    def options(
        self, tool_ids: tuple[str, ...], labels: dict[str, str], away: str
    ) -> SessionOptions:
        """Greet first, then say what happened away; the agent always speaks first."""
        greeting = CallGreeting(self._projects).spoken()
        return SessionOptions(
            CallSeed(self._projects, self._workers).prompt(labels),
            tool_ids,
            first_message=" ".join(filter(None, (greeting, away))),
        )
