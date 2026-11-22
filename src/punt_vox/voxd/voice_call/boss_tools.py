"""The boss's client tools: delegation over the provider-neutral worker surface."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, ClassVar, Final, Self, final

from punt_vox.voxd.voice_agent.contracts import ClientTool
from punt_vox.voxd.voice_call.result_cache import ToolResultCache
from punt_vox.voxd.voice_call.worker_brief import WorkerBrief

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from punt_vox.voxd.voice_call.contracts import ProjectAllowlist, WorkerControl
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["BossTools"]

_ID: Final = {"type": "string", "description": "The worker_id a tool returned"}


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

    __slots__ = ("_cache", "_projects", "_workers")
    _workers: WorkerControl
    _projects: ProjectAllowlist
    _cache: ToolResultCache

    def __new__(cls, workers: WorkerControl, projects: ProjectAllowlist) -> Self:
        """Bind the worker surface, its allowlist and a fresh per-call result cache."""
        self = super().__new__(cls)
        self._workers, self._projects = workers, projects
        self._cache = ToolResultCache()
        return self

    @property
    def replayed(self) -> int:
        """Return how many tool calls were answered from an earlier execution."""
        return self._cache.hits

    async def answer(self, name: str, arguments: dict[str, object]) -> tuple[str, bool]:
        """Return the JSON result for the agent and whether it reports an error."""
        operations: dict[str, Callable[[dict[str, object]], Awaitable[object]]] = {
            "start_worker": self._start,
            "steer_worker": self._steer,
            "worker_status": self._status,
            "list_workers": self._list,
            "stop_worker": self._stop,
            "remain_silent": self._silent,
        }
        operation = operations.get(name)
        if operation is None:
            return json.dumps({"error": f"unknown tool {name}"}), True

        arguments = self._with_defaults(name, arguments)

        async def execute() -> str:
            return json.dumps(await operation(arguments), ensure_ascii=False)

        try:
            result = await self._cache.run(
                ToolResultCache.key(name, arguments),
                execute,
                durable=name in self._DURABLE,
            )
        except (
            ValueError,
            KeyError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False), True
        return result, False

    def _with_defaults(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """Fill omitted defaults, so a re-ask that spells one out is the same ask."""
        defaults: dict[str, object] = {}
        if name == "steer_worker":
            defaults["urgent"] = False
        elif name == "start_worker" and len(repos := self._projects.repos()) == 1:
            defaults["project"] = repos[0]
        return {**defaults, **arguments}

    async def _start(self, arguments: dict[str, object]) -> object:
        task = self._text(arguments, "task")
        project = arguments.get("project")
        if project is None:
            projects = self._projects.repos()
            if len(projects) != 1:
                raise ValueError(f"name a project; allowlisted: {list(projects)}")
            project = projects[0]
        if not isinstance(project, str):
            raise ValueError("project must be a string")
        return WorkerBrief.summary(await self._workers.start(task, project))

    async def _steer(self, arguments: dict[str, object]) -> object:
        urgent = arguments.get("urgent", False)
        if not isinstance(urgent, bool):
            raise ValueError("urgent must be a boolean")
        worker = await self._find(arguments)
        words = self._text(arguments, "words")
        return await self._workers.steer(worker["id"], words, urgent=urgent)

    async def _status(self, arguments: dict[str, object]) -> object:
        return WorkerBrief.detail(await self._find(arguments))

    async def _list(self, _arguments: dict[str, object]) -> object:
        return [WorkerBrief.summary(w) for w in await self._workers.workers()]

    async def _stop(self, arguments: dict[str, object]) -> object:
        worker = await self._find(arguments)
        return WorkerBrief.summary(await self._workers.stop(worker["id"]))

    @staticmethod
    async def _silent(_arguments: dict[str, object]) -> object:
        return {"silent": True}

    async def _find(self, arguments: dict[str, object]) -> WorkerPayload:
        """Resolve a full ID or a unique prefix, since IDs are spoken aloud."""
        wanted = self._text(arguments, "worker_id")
        workers = await self._workers.workers()
        exact = [w for w in workers if w["id"] == wanted]
        prefixed = [
            w for w in workers if len(wanted) >= 4 and w["id"].startswith(wanted)
        ]
        matches = exact or prefixed
        if len(matches) != 1:
            raise ValueError(f"no single worker matches {wanted!r}")
        return matches[0]

    @staticmethod
    def _text(arguments: dict[str, object], field: str) -> str:
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value
