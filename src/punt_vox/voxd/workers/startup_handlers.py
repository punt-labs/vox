"""Gate worker-dependent RPCs without delaying daemon startup."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Self, final

from punt_vox.client_errors import VoxdRecoveryPendingError
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler

logger = logging.getLogger(__name__)
_RECOVERY_TIMEOUT_S = 2.0


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

    async def __call__(
        self,
        msg: dict[str, object],  # PY-TS-14: route handlers validate their own JSON.
        websocket: WebSocket,
    ) -> None:
        """Wait for durable worker state before dispatching."""
        if msg.get("type") == "hook_event" and msg.get("worker_only") is not True:
            await self._handler(msg, websocket)
            return
        reply = WireReply(websocket, str(msg.get("id", "")))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RECOVERY_TIMEOUT_S
        try:
            async with asyncio.timeout_at(deadline):
                ready = await asyncio.shield(asyncio.wrap_future(self._recovery))
                if loop.time() >= deadline:
                    raise TimeoutError
        except TimeoutError:
            await reply.send(
                {
                    "type": "error",
                    "code": VoxdRecoveryPendingError.CODE,
                    "message": "Worker recovery pending; retry the request",
                }
            )
            return
        if not ready:
            await reply.error("Worker startup recovery failed; check the daemon log")
            return
        await self._handler(msg, websocket)
