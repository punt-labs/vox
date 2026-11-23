"""Gate worker-dependent RPCs without delaying daemon startup."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler

logger = logging.getLogger(__name__)


@final
class StartupHandlers:
    """Share one worker recovery across requests and client disconnects."""

    __slots__ = ("_handler", "_recovery")
    _handler: MessageHandler
    _recovery: Future[bool]

    def __new__(cls, handler: MessageHandler, recovery: Future[bool]) -> Self:
        """Bind a single handler to the common recovery barrier."""
        self = super().__new__(cls)
        self._handler, self._recovery = handler, recovery
        return self

    @classmethod
    def start(
        cls, recover: Callable[[], None], handlers: dict[str, MessageHandler]
    ) -> dict[str, MessageHandler]:
        """Start recovery even if no worker clients connect."""
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vox-recovery")
        ready: Future[bool] = Future()
        try:
            recovery = executor.submit(recover)
            recovery.add_done_callback(lambda task: cls._record_result(ready, task))
        finally:
            executor.shutdown(wait=False)
        return {route: cls(handler, ready) for route, handler in handlers.items()}

    @staticmethod
    def _record_result(ready: Future[bool], recovery: Future[None]) -> None:
        if (error := recovery.exception()) is not None:
            logger.error("Worker startup recovery failed (%s)", type(error).__name__)
        ready.set_result(error is None)

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Wait for durable worker state before dispatching."""
        if not await asyncio.shield(asyncio.wrap_future(self._recovery)):
            await WireReply(websocket, str(msg.get("id", ""))).error(
                "Worker startup recovery failed; check the daemon log"
            )
            return
        await self._handler(msg, websocket)
