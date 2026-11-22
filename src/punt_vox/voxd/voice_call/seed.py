"""The boss prompt: its role, the repositories it may use, and the live workers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.voice_call.worker_brief import WorkerBrief

if TYPE_CHECKING:
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["CallSeed"]

_ROLE: Final = """\
You are Vox, the voice of a boss that delegates software work to persistent \
coding workers. You never do the work yourself and never claim a result you \
have not read from a tool. Keep every reply to one or two short spoken \
sentences, with no markdown, lists or code.

- To get work done, call start_worker with the person's task in their own \
words. A worker keeps running after this call ends.
- To pass the person's words to a running worker, call steer_worker with the \
words verbatim. Set urgent only when they want the worker to stop what it is \
doing first.
- To answer how a worker is doing, call worker_status; to say which workers \
exist, call list_workers; to stop one, call stop_worker.
- When the person is not talking to you, or asks you to be quiet, call \
remain_silent and then say nothing.
- If a tool call is interrupted and the person asks again, call the same tool \
again with the same arguments; it answers at once.
"""


@final
class CallSeed:
    """Render the prompt, capped so it never crowds out the conversation."""

    __slots__ = ("_projects", "_workers")
    _projects: tuple[str, ...]
    _workers: list[WorkerPayload]
    LIMIT: Final = 10_000  # bytes, UTF-8

    def __new__(cls, projects: tuple[str, ...], workers: list[WorkerPayload]) -> Self:
        """Bind the allowlisted repositories and the registry snapshot."""
        self = super().__new__(cls)
        self._projects = projects
        self._workers = sorted(workers, key=lambda w: w["created_at"], reverse=True)
        return self

    def prompt(self) -> str:
        """Return the prompt, dropping the oldest workers until it fits LIMIT."""
        briefs = [WorkerBrief.detail(worker) for worker in self._workers]
        text = self._render(briefs)
        while len(text.encode()) > self.LIMIT and briefs:
            briefs.pop()
            text = self._render(briefs)
        return self._cut(text)

    def _render(self, briefs: list[dict[str, object]]) -> str:
        projects = json.dumps(list(self._projects), ensure_ascii=False)
        lines = [_ROLE, f"Repositories a worker may use: {projects}"]
        if len(self._projects) == 1:
            lines.append("start_worker uses that repository when no project is given.")
        state = {
            "workers": briefs,
            "older_workers_omitted": len(self._workers) - len(briefs),
        }
        lines.append(
            "Workers right now, newest first (JSON): "
            + json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        )
        return "\n".join(lines)

    def _cut(self, text: str) -> str:
        return text.encode()[: self.LIMIT].decode(errors="ignore")
