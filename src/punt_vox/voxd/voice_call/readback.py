"""The daemon's own words for a request the person is asked to confirm."""

from __future__ import annotations

from pathlib import PurePath
from typing import TYPE_CHECKING, Final, Self, cast, final

from punt_vox.voxd.voice_call.worker_brief import WorkerBrief

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.worker_directory import WorkerDirectory
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["ReadBack"]

_ASK: Final = "Say yes to go ahead."
_NO_PROMPT: Final = "a permission request voxd cannot read"


@final
class ReadBack:
    """Build what the agent must read aloud, from the request and the worker.

    The text is the daemon's, never the model's: a model that was fooled would
    read back something harmless. For an approval it is the worker's own
    permission prompt for the exact question the person is answering.
    """

    __slots__ = ("_directory",)
    _directory: WorkerDirectory

    def __new__(cls, directory: WorkerDirectory) -> Self:
        """Bind the worker directory that resolves spoken identities."""
        self = super().__new__(cls)
        self._directory = directory
        return self

    async def of(self, name: str, request: dict[str, object]) -> str:
        """Return the read-back for the tool ``name`` and its resolved request."""
        if name == "start_worker":
            return self._start(request)
        worker = await self._directory.find(str(request["worker_id"]))
        task = WorkerBrief.summary(worker)["task"]
        if name == "stop_worker":
            return f"Stop the worker on this task: {task}. {_ASK}"
        if request.get("choice") == "approve":
            asked = f"The worker on {task} asks: {self._prompt(worker)}"
            return f"{asked} Say yes to approve."
        return f"Send the worker on {task} these words: {request['words']}. {_ASK}"

    async def is_pending(self, request: dict[str, object]) -> bool:
        """Return whether the worker is showing the prompt the request answers."""
        worker = await self._directory.find(str(request["worker_id"]))
        pending = worker["last_report"].get("pending_decision")
        if not isinstance(pending, dict):
            return False
        seq = cast("dict[str, object]", pending).get("seq")
        return seq is not None and seq == request.get("prompt_seq")

    @staticmethod
    def _start(request: dict[str, object]) -> str:
        project = PurePath(str(request.get("project", ""))).name or "the repository"
        return f"Start a worker in {project} for this task: {request['task']}. {_ASK}"

    @staticmethod
    def _prompt(worker: WorkerPayload) -> str:
        pending = worker["last_report"].get("pending_decision")
        if isinstance(pending, dict):
            message = cast("dict[str, object]", pending).get("message")
            if isinstance(message, str) and message.strip():
                return message
        return _NO_PROMPT
