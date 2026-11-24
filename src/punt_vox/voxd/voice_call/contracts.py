"""The seams a voice call depends on, so tools never reach a concrete provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.workers.steering_types import DecisionChoice, SteerReceipt
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["CallInput", "ProjectAllowlist", "WorkerControl"]


class WorkerControl(Protocol):
    """The provider-neutral worker surface the boss tools may call."""

    async def start(self, task: str, project: str) -> WorkerPayload:
        """Start one persistent worker on the default provider and harness."""
        ...

    async def stop(self, worker_id: str) -> WorkerPayload:
        """Stop one worker, keeping its worktree and branch."""
        ...

    async def workers(self) -> list[WorkerPayload]:
        """Return workers with last_report.prompt_epoch identifying their prompt."""
        ...

    async def steer(
        self,
        worker_id: str,
        words: str,
        *,
        urgent: bool,
        prompt_epoch: int,
        choice: DecisionChoice | None = None,  # None submits ordinary follow-up text.
        prompt_seq: int
        | None = None,  # None is ordinary text, not a permission answer.
    ) -> SteerReceipt:
        """Deliver words verbatim and report whether the worker heard them."""
        ...


class ProjectAllowlist(Protocol):
    """The repositories a worker may be started in."""

    def repos(self) -> tuple[str, ...]:
        """Return the allowlisted repository paths in a stable order."""
        ...


class CallInput(Protocol):
    """A source of 16 kHz mono S16LE chunks paced at real time.

    ``open`` acquires whatever the source needs before the call is dialled,
    so a source that cannot deliver refuses the call; ``close`` releases it
    however the call ended.
    """

    def open(self) -> None:
        """Acquire the source, or raise so the call never starts."""
        ...

    def chunks(self) -> AsyncIterator[bytes]:
        """Yield caller audio until the call stops consuming."""
        ...

    def close(self) -> None:
        """Release the source; safe after a failed or skipped open."""
        ...
