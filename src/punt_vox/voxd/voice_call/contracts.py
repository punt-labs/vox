"""The seams a Mode B call depends on, so tools never reach a concrete provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.workers.steering_types import SteerReceipt
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["CallInput", "WorkerControl"]


class WorkerControl(Protocol):
    """The provider-neutral worker surface the boss tools may call."""

    def projects(self) -> tuple[str, ...]:
        """Return the allowlisted repositories a worker may be started in."""
        ...

    async def start(self, task: str, project: str) -> WorkerPayload:
        """Start one persistent worker on the default provider and harness."""
        ...

    async def stop(self, worker_id: str) -> WorkerPayload:
        """Stop one worker, keeping its worktree and branch."""
        ...

    async def workers(self) -> list[WorkerPayload]:
        """Return every known worker, reconciled with its live session."""
        ...

    async def steer(self, worker_id: str, words: str, *, urgent: bool) -> SteerReceipt:
        """Deliver words verbatim and report whether the worker heard them."""
        ...


class CallInput(Protocol):
    """A source of 16 kHz mono S16LE chunks paced at real time."""

    def chunks(self) -> AsyncIterator[bytes]:
        """Yield caller audio until the call stops consuming."""
        ...
