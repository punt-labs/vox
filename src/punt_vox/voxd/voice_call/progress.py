"""Provider-neutral, bounded live worker reports and conversational scheduling."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, nullcontext
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_call.progress_types import WorkerProgress

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from contextlib import AbstractAsyncContextManager

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

    __slots__ = ("_activity", "_events", "_reported")
    _activity: float
    _events: ProgressEvents
    _reported: dict[str, int]
    LULL = 2.0

    def __new__(cls, events: ProgressEvents) -> Self:
        """Bind the live stream and an idle activity clock."""
        self = super().__new__(cls)
        self._events = events
        self._activity = 0.0
        self._reported = {}
        return self

    @property
    def reported(self) -> dict[str, int]:
        """Return result sequences successfully submitted for speech."""
        return self._reported.copy()

    def activity(self) -> None:
        """Postpone result announcements while either participant is speaking."""
        self._activity = asyncio.get_running_loop().time()

    def subscribe(self) -> AbstractAsyncContextManager[asyncio.Queue[WorkerProgress]]:
        """Start observing before taking the worker snapshot."""
        return self._events.subscribe()

    async def run(
        self,
        conv: Conversation,
        queue: asyncio.Queue[WorkerProgress] | None = None,
        covered: dict[str, int] | None = None,
    ) -> None:
        """Decisions bypass queued results and the lull timer."""
        self.activity()
        async with self.subscribe() if queue is None else nullcontext(queue) as inbox:
            await self._deliver(conv, inbox, covered if covered is not None else {})

    async def _deliver(
        self,
        conv: Conversation,
        inbox: asyncio.Queue[WorkerProgress],
        covered: dict[str, int],
    ) -> None:
        pending: dict[str, WorkerProgress] = {}
        while True:
            try:
                delay = self._lull_remaining(self._activity) if pending else None
                async with asyncio.timeout(delay):
                    event = await inbox.get()
            except TimeoutError:
                if self._lull_remaining(self._activity) > 0:
                    continue
                await self._speak(conv, pending.pop(next(iter(pending))))
                continue
            if event.seq <= covered.get(event.worker_id, 0):
                continue
            await self._context(conv, event)
            pending.pop(event.worker_id, None)
            if event.kind == "needs_decision":
                await self._speak(conv, event)
            else:
                pending[event.worker_id] = event
                if len(pending) > ProgressEvents.LIMIT:
                    del pending[next(iter(pending))]

    @classmethod
    def _lull_remaining(cls, activity: float) -> float:
        return max(0.0, cls.LULL - (asyncio.get_running_loop().time() - activity))

    @staticmethod
    async def _context(conv: Conversation, event: WorkerProgress) -> None:
        await conv.send_context(
            json.dumps(
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
        )

    async def _speak(self, conv: Conversation, event: WorkerProgress) -> None:
        await conv.send_text(
            f"[voxd worker update, not the person] Worker {event.worker_id} "
            f"event {event.seq}: {event.kind}. "
            "Briefly report the result from the matching background update; "
            "if a decision is needed, ask the person now."
        )
        if event.kind == "finished":
            self._reported[event.worker_id] = max(
                event.seq, self._reported.get(event.worker_id, 0)
            )
        self.activity()
