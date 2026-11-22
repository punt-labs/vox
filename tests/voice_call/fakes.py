"""Test doubles for the Mode B call's worker surface and playback stream."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from punt_vox.voxd.workers.steering_types import SteerReceipt
    from punt_vox.voxd.workers.types import WorkerPayload


def payload(worker_id: str, task: str = "edit", **report: object) -> WorkerPayload:
    return {
        "id": worker_id,
        "provider": "tmux",
        "harness": "claude",
        "session": f"vox-{worker_id}",
        "project": "/repo",
        "worktree": f"/wt/{worker_id}",
        "branch": f"vox/{worker_id}",
        "task": task,
        "state": "ready",
        "created_at": f"2026-11-22T00:00:{len(worker_id):02d}Z",
        "last_report": dict(report),
    }


class FakeWorkers:
    """Record calls; ``gate`` holds start open to model a slow worker launch."""

    def __init__(self, projects: tuple[str, ...] = ("/repo",)) -> None:
        self.allowed = projects
        self.started: list[tuple[str, str]] = []
        self.steered: list[tuple[str, str, bool]] = []
        self.stopped: list[str] = []
        self.known: list[WorkerPayload] = []
        self.gate = asyncio.Event()
        self.gate.set()

    def projects(self) -> tuple[str, ...]:
        return self.allowed

    async def start(self, task: str, project: str) -> WorkerPayload:
        self.started.append((task, project))
        await self.gate.wait()
        worker = payload(f"w{len(self.started)}-0000-aaaa", task)
        self.known.append(worker)
        return worker

    async def stop(self, worker_id: str) -> WorkerPayload:
        self.stopped.append(worker_id)
        worker = next(w for w in self.known if w["id"] == worker_id)
        return {**worker, "state": "stopped"}

    async def workers(self) -> list[WorkerPayload]:
        return list(self.known)

    async def steer(self, worker_id: str, words: str, *, urgent: bool) -> SteerReceipt:
        self.steered.append((worker_id, words, urgent))
        return {
            "worker_id": worker_id,
            "heard": True,
            "receipt_seq": 1,
            "urgent": urgent,
        }


class FakeSink:
    def __init__(self) -> None:
        self.fed: list[bytes] = []
        self.flushed = 0

    def feed(self, pcm: bytes) -> None:
        self.fed.append(pcm)

    def flush(self) -> None:
        self.flushed += 1


class FakePlayback:
    """Hand out one fake sink per stream and count releases of the mutex."""

    def __init__(self) -> None:
        self.sinks: list[FakeSink] = []
        self.released = 0

    @asynccontextmanager
    async def stream_pcm(self) -> AsyncGenerator[FakeSink]:
        sink = FakeSink()
        self.sinks.append(sink)
        try:
            yield sink
        finally:
            self.released += 1
