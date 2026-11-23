"""Serialize account operations that can outlive the requesting call."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_call.connector import CallConnector

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.voice_agent.contracts import ClientTool
    from punt_vox.voxd.voice_call.connector import AgentConnector


@final
class AgentOperations:
    """Hold the operation lock until the worker thread settles."""

    __slots__ = ("_connector", "_operation", "_prepared")
    _connector: CallConnector
    _operation: asyncio.Lock
    _prepared: bool

    def __new__(cls, agent: AgentConnector | None) -> Self:
        """Bind the account connector and its operation lock."""
        self = super().__new__(cls)
        self._connector, self._operation = CallConnector(agent), asyncio.Lock()
        self._prepared = agent is not None
        return self

    async def prepare(self) -> None:
        """Validate account configuration before starting a call."""
        if not self._prepared:
            await self._run(self._connector.agent)
            self._prepared = True

    async def connection(
        self, tools: tuple[ClientTool, ...]
    ) -> tuple[str, tuple[str, ...]]:
        """Sign a conversation URL without blocking the event loop."""
        return await self._run(lambda: self._connector.agent().connection(tools))

    async def teardown(self) -> None:
        """Delete registration after any outstanding connection settles."""
        await self._run(lambda: self._connector.agent().teardown())

    async def _run[T](self, operation: Callable[[], T]) -> T:
        await self._operation.acquire()
        task = asyncio.create_task(asyncio.to_thread(operation))
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    def _finished[T](self, task: asyncio.Task[T]) -> None:
        self._operation.release()
        if not task.cancelled():
            task.exception()
