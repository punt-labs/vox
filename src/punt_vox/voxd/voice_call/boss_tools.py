"""The boss's client tools: delegation over the provider-neutral worker surface."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar, Final, Self, final

from punt_vox.voxd.voice_agent.contracts import ClientTool
from punt_vox.voxd.voice_call.lookup.tools import PROVIDER_DEADLINE_S, ResearchTools
from punt_vox.voxd.voice_call.provenance import CallProvenance
from punt_vox.voxd.voice_call.request_guard import RequestGuard
from punt_vox.voxd.voice_call.steering import SteeringTool
from punt_vox.voxd.voice_call.tool_answer import ToolAnswer
from punt_vox.voxd.voice_call.tool_fields import ToolFields
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
            "Report one worker's state (working, blocked, idle or finished), the "
            "tool it is running, the files it is touching, any open failure, how "
            "long since it last reported, and its latest progress.",
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
        ClientTool(
            "search_web",
            "Search the web for current facts and news. Returns a few short, "
            "quoted results.",
            _schema(
                {"query": {"type": "string", "description": "What to search for"}},
                "query",
            ),
            response_timeout_secs=PROVIDER_DEADLINE_S,
        ),
        ClientTool(
            "read_docs",
            "Ask a question about a public code repository's documentation. "
            "Returns a short, quoted answer. Infer the repository owner/name "
            "yourself for well-known projects; never ask the person for an owner "
            "or a name you can work out.",
            _schema(
                {
                    "repo": {
                        "type": "string",
                        "description": (
                            "Public repository as owner/name, such as "
                            "modelcontextprotocol/python-sdk. A plain name is "
                            "looked up for you; if unsure, call search_web first"
                        ),
                    },
                    "question": {"type": "string", "description": "The question"},
                },
                "repo",
                "question",
            ),
            response_timeout_secs=PROVIDER_DEADLINE_S,
        ),
    )
    # Tools that change a worker: a re-ask must replay, never repeat, them.
    _DURABLE: ClassVar[frozenset[str]] = frozenset(
        {"start_worker", "steer_worker", "stop_worker"}
    )

    # Tools that start, change or stop a worker: held for the person's spoken
    # yes whenever untrusted text has reached the conversation.
    _CONFIRMED: ClassVar[frozenset[str]] = frozenset(
        {"start_worker", "steer_worker", "stop_worker"}
    )

    # Tools whose results carry worker-written text.
    _UNTRUSTED: ClassVar[frozenset[str]] = frozenset(
        {"worker_status", "list_workers", "search_web", "read_docs"}
    )
    # Tools whose arguments the call never keeps in its status or trace.
    _PRIVATE: ClassVar[frozenset[str]] = frozenset({"search_web", "read_docs"})

    __slots__ = (
        "_answer",
        "_clock",
        "_directory",
        "_guard",
        "_projects",
        "_research",
        "_workers",
    )
    _clock: Callable[[], float]
    _directory: WorkerDirectory
    _workers: WorkerControl
    _projects: ProjectAllowlist
    _answer: ToolAnswer
    _guard: RequestGuard
    _research: ResearchTools

    def __new__(
        cls,
        workers: WorkerControl,
        projects: ProjectAllowlist,
        *,
        spoken_labels: dict[str, str] | None = None,
        research: ResearchTools | None = None,  # None: no search or docs set up.
        clock: Callable[[], float] = time.time,
    ) -> Self:
        """Bind the worker surface, its allowlist and a fresh per-call result cache."""
        self = super().__new__(cls)
        self._workers, self._projects = workers, projects
        self._clock = clock
        self._directory = WorkerDirectory(workers, spoken_labels)
        self._answer = ToolAnswer()
        self._research = research if research is not None else ResearchTools()
        self._guard = RequestGuard(CallProvenance(), self._directory, projects)
        return self

    @property
    def guard(self) -> RequestGuard:
        """Return the guard that holds the call's provenance and its gate."""
        return self._guard

    @classmethod
    def keeps_arguments(cls, name: str) -> bool:
        """Return whether a call may show a tool's arguments in its status."""
        return name not in cls._PRIVATE

    @classmethod
    def tool_names(cls) -> frozenset[str]:
        """Return the names of the client tools voxd runs itself."""
        return frozenset(tool.name for tool in cls.CATALOG)

    @property
    def replayed(self) -> int:
        """Return how many tool calls were answered from an earlier execution."""
        return self._answer.replayed

    async def answer(self, name: str, arguments: dict[str, object]) -> tuple[str, bool]:
        """Return the JSON result for the agent and whether it reports an error.

        :class:`ToolAnswer` classifies the outcome: a refusal is spoken
        verbatim, a daemon-side fault is spoken without its path.
        """
        steering = SteeringTool(self._workers, self._directory)
        operations: dict[str, Callable[[dict[str, object]], Awaitable[object]]] = {
            "start_worker": self._start,
            "steer_worker": steering.send,
            "worker_status": self._status,
            "list_workers": self._list,
            "stop_worker": self._stop,
            "remain_silent": self._silent,
            "search_web": self._research.search_web,
            "read_docs": self._research.read_docs,
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

        guard = self._guard.for_tool(name) if name in self._CONFIRMED else None
        answered = await self._answer.run(
            name, request, operation, durable=name in self._DURABLE, guard=guard
        )
        if name in self._UNTRUSTED:  # Worker text is now in the conversation.
            self._guard.provenance.taint()
        return answered

    def _with_defaults(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """Normalize launch defaults before deriving the result-cache identity."""
        defaults: dict[str, object] = {}
        if name == "start_worker" and len(repos := self._projects.repos()) == 1:
            defaults["project"] = repos[0]
        return {**defaults, **arguments}

    async def _start(self, arguments: dict[str, object]) -> object:
        task, project = ToolFields.start(arguments, self._projects)
        return WorkerBrief.summary(await self._workers.start(task, project))

    async def _status(self, arguments: dict[str, object]) -> object:
        worker = await self._directory.find(ToolFields.text(arguments, "worker_id"))
        return WorkerBrief.detail(worker, self._clock())

    async def _list(self, _arguments: dict[str, object]) -> object:
        return [WorkerBrief.summary(w) for w in await self._workers.workers()]

    async def _stop(self, arguments: dict[str, object]) -> object:
        worker = await self._directory.find(ToolFields.text(arguments, "worker_id"))
        return WorkerBrief.summary(await self._workers.stop(worker["id"]))

    @staticmethod
    async def _silent(_arguments: dict[str, object]) -> object:
        return {"silent": True}
