"""Provider-neutral, bounded live worker reports and conversational scheduling."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_call.progress_types import WorkerProgress

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from punt_vox.voxd.voice_agent.session import Conversation


@final
class ProgressEvents:
    """Fan out without backpressure; absent or slow listeners lose reports."""

    __slots__ = ("_subscribers",)
    _subscribers: set[asyncio.Queue[WorkerProgress]]
    LIMIT = 32

    def __new__(cls) -> Self:
        """Create a stream with no live listeners."""
        self = super().__new__(cls)
        self._subscribers = set()
        return self

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[WorkerProgress]]:
        """Register only for the lifetime of a live call."""
        queue: asyncio.Queue[WorkerProgress] = asyncio.Queue(self.LIMIT)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.remove(queue)

    def publish(self, event: WorkerProgress) -> None:
        """Keep the newest reports if a listener falls behind."""
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


@final
class CallProgress:
    """Send hidden context immediately, speaking results only at a lull."""

    __slots__ = ("_activity", "_events")
    _activity: float
    _events: ProgressEvents
    LULL = 2.0

    def __new__(cls, events: ProgressEvents) -> Self:
        """Bind the live stream and an idle activity clock."""
        self = super().__new__(cls)
        self._events = events
        self._activity = 0.0
        return self

    def activity(self) -> None:
        """Postpone result announcements while either participant is speaking."""
        self._activity = asyncio.get_running_loop().time()

    async def run(self, conv: Conversation) -> None:
        """Decisions bypass queued results and the lull timer."""
        self.activity()
        pending: list[WorkerProgress] = []
        async with self._events.subscribe() as queue:
            while True:
                delay = max(
                    0.0,
                    self.LULL - (asyncio.get_running_loop().time() - self._activity),
                )
                try:
                    async with asyncio.timeout(delay if pending else None):
                        event = await queue.get()
                except TimeoutError:
                    if asyncio.get_running_loop().time() - self._activity < self.LULL:
                        continue
                    await self._nudge(conv, pending.pop(0))
                    self.activity()
                    continue
                text = json.dumps(
                    {
                        "source": "voxd worker update",
                        "worker_id": event.worker_id,
                        "seq": event.seq,
                        "kind": event.kind,
                        "notification_type": event.notification_type,
                        "report": event.text,
                    },
                    ensure_ascii=False,
                )
                await conv.send_context(text)
                if event.kind == "needs_decision":
                    pending = [p for p in pending if p.worker_id != event.worker_id]
                    await self._nudge(conv, event)
                    self.activity()
                else:
                    pending = [p for p in pending if p.worker_id != event.worker_id]
                    pending.append(event)
                    pending = pending[-ProgressEvents.LIMIT :]

    @staticmethod
    async def _nudge(conv: Conversation, event: WorkerProgress) -> None:
        await conv.send_text(
            f"[voxd worker update, not the person] Worker {event.worker_id} "
            f"event {event.seq}: {event.kind}. "
            "Briefly report the result from the matching background update; "
            "if a decision is needed, ask the person now."
        )
