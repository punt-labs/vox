"""The boss's client tools: delegation over the provider-neutral worker surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final, Self, final

from punt_vox.voxd.voice_agent.contracts import ClientTool
from punt_vox.voxd.voice_call.steering import SteeringTool
from punt_vox.voxd.voice_call.tool_answer import ToolAnswer
from punt_vox.voxd.voice_call.tool_reply import ToolReply
from punt_vox.voxd.voice_call.worker_brief import WorkerBrief
from punt_vox.voxd.voice_call.worker_directory import WorkerDirectory

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from punt_vox.voxd.voice_call.contracts import ProjectAllowlist, WorkerControl
    from punt_vox.voxd.voice_call.tool_answer import Request

__all__ = ["BossTools"]

_ID: Final = {
    "type": "string",
    "description": (
        "A worker_id a tool returned, or a spoken worker label from this call's opener"
    ),
}


_NO_REPOS: Final = (
    "no repository is allowlisted for workers; "
    "run `vox worker allow <path>` on the daemon host"
)


def _schema(properties: dict[str, object], *required: str) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(required)}


@final
class BossTools:
    """Run one call's tool requests, each at most once per distinct request."""

    CATALOG: ClassVar[tuple[ClientTool, ...]] = (
        ClientTool(
            "start_worker",
            "Start one persistent coding worker per task in an allowlisted repository.",
            _schema(
                {
                    "task": {"type": "string", "description": "The task, verbatim"},
                    "project": {
                        "type": "string",
                        "description": "Absolute repository path; omit if only one",
                    },
                },
                "task",
            ),
        ),
        ClientTool(
            "steer_worker",
            "Pass the person's words verbatim to a running worker.",
            _schema(
                {
                    "worker_id": _ID,
                    "words": {"type": "string", "description": "Words, verbatim"},
                    "choice": {
                        "type": "string",
                        "enum": ["approve", "deny"],
                        "description": (
                            "Required for permission_prompt: approve=yes, deny=no. "
                            "Also pass prompt_seq for the question you asked. "
                            "Omit for ordinary input."
                        ),
                    },
                    "prompt_seq": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Required with choice: seq of the permission prompt you "
                            "asked the person about, from the voxd update or "
                            "worker_status. Never substitute a newer prompt's seq."
                        ),
                    },
                    "urgent": {
                        "type": "boolean",
                        "description": "Interrupt the worker's current step first",
                    },
                },
                "worker_id",
                "words",
            ),
        ),
        ClientTool(
            "worker_status",
            "Report one worker's state and latest progress.",
            _schema({"worker_id": _ID}, "worker_id"),
        ),
        ClientTool("list_workers", "List every worker and its state.", _schema({})),
        ClientTool(
            "stop_worker",
            "Stop a worker. Its branch and worktree are kept.",
            _schema({"worker_id": _ID}, "worker_id"),
        ),
        ClientTool(
            "remain_silent",
            "Stay quiet: the person is not talking to you or asked for quiet.",
            _schema({}),
        ),
    )
    # Tools that change a worker: a re-ask must replay, never repeat, them.
    _DURABLE: ClassVar[frozenset[str]] = frozenset(
        {"start_worker", "steer_worker", "stop_worker"}
    )

    __slots__ = ("_answer", "_directory", "_projects", "_workers")
    _directory: WorkerDirectory
    _workers: WorkerControl
    _projects: ProjectAllowlist
    _answer: ToolAnswer

    def __new__(
        cls,
        workers: WorkerControl,
        projects: ProjectAllowlist,
        *,
        spoken_labels: dict[str, str] | None = None,
    ) -> Self:
        """Bind the worker surface, its allowlist and a fresh per-call result cache."""
        self = super().__new__(cls)
        self._workers, self._projects = workers, projects
        self._directory = WorkerDirectory(workers, spoken_labels)
        self._answer = ToolAnswer()
        return self

    @property
    def replayed(self) -> int:
        """Return how many tool calls were answered from an earlier execution."""
        return self._answer.replayed

    async def answer(self, name: str, arguments: dict[str, object]) -> ToolReply:
        """Return the JSON result for the agent, with its error when it failed."""
        steering = SteeringTool(self._workers, self._directory)
        operations: dict[str, Callable[[dict[str, object]], Awaitable[object]]] = {
            "start_worker": self._start,
            "steer_worker": steering.send,
            "worker_status": self._status,
            "list_workers": self._list,
            "stop_worker": self._stop,
            "remain_silent": self._silent,
        }
        operation = operations.get(name)
        if operation is None:
            return ToolAnswer.error(f"unknown tool {name}")

        async def request() -> Request:
            if name == "steer_worker":
                resolved = await steering.resolve(arguments)
                return steering.identity(resolved), resolved
            resolved = self._with_defaults(name, arguments)
            return resolved, resolved

        return await self._answer.run(
            name, request, operation, durable=name in self._DURABLE
        )

    def _with_defaults(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """Normalize launch defaults before deriving the result-cache identity."""
        defaults: dict[str, object] = {}
        if name == "start_worker" and len(repos := self._projects.repos()) == 1:
            defaults["project"] = repos[0]
        return {**defaults, **arguments}

    async def _start(self, arguments: dict[str, object]) -> object:
        task = self._text(arguments, "task")
        allowlisted = list(self._projects.repos())
        if not allowlisted:  # Whatever was named: nothing can start.
            raise ValueError(_NO_REPOS)
        project = arguments.get("project")
        if project is None:  # Several repos, so no default was filled in.
            raise ValueError(f"name a project; allowlisted: {allowlisted}")
        if not isinstance(project, str):
            raise ValueError("project must be a string")
        return WorkerBrief.summary(await self._workers.start(task, project))

    async def _status(self, arguments: dict[str, object]) -> object:
        worker = await self._directory.find(self._text(arguments, "worker_id"))
        return WorkerBrief.detail(worker)

    async def _list(self, _arguments: dict[str, object]) -> object:
        return [WorkerBrief.summary(w) for w in await self._workers.workers()]

    async def _stop(self, arguments: dict[str, object]) -> object:
        worker = await self._directory.find(self._text(arguments, "worker_id"))
        return WorkerBrief.summary(await self._workers.stop(worker["id"]))

    @staticmethod
    async def _silent(_arguments: dict[str, object]) -> object:
        return {"silent": True}

    @staticmethod
    def _text(arguments: dict[str, object], field: str) -> str:
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value
