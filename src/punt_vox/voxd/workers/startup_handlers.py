"""Gate worker-dependent RPCs without delaying daemon startup."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler


@final
class StartupHandlers:
    """Share one worker recovery across requests and client disconnects."""

    __slots__ = ("_handler", "_recovery")
    _handler: MessageHandler
    _recovery: Future[None]

    def __new__(cls, handler: MessageHandler, recovery: Future[None]) -> Self:
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
        try:
            recovery = executor.submit(recover)
        finally:
            executor.shutdown(wait=False)
        return {route: cls(handler, recovery) for route, handler in handlers.items()}

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Wait for durable worker state before dispatching."""
        await asyncio.shield(asyncio.wrap_future(self._recovery))
        await self._handler(msg, websocket)
