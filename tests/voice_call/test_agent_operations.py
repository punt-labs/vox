from __future__ import annotations

import asyncio
import threading
from unittest.mock import Mock

import pytest

from punt_vox.voxd.voice_call.agent_operations import AgentOperations


async def test_teardown_can_retry_after_connection_failure() -> None:
    agent = Mock()
    agent.connection.side_effect = ValueError("cannot connect")
    operations = AgentOperations(agent)

    async with asyncio.timeout(5):
        with pytest.raises(ValueError, match="cannot connect"):
            await operations.connection(())
        await operations.teardown()

    agent.teardown.assert_called_once_with()


async def test_retiring_never_raises_into_the_caller() -> None:
    agent = Mock()
    agent.retire_stale.side_effect = RuntimeError("boom")
    operations = AgentOperations(agent)

    async with asyncio.timeout(5):
        await operations.retire()

    agent.retire_stale.assert_called_once_with()


async def test_a_connection_does_not_wait_for_a_retirement_in_progress() -> None:
    release = threading.Event()
    entered = threading.Event()
    agent = Mock()

    def slow_retire() -> None:
        entered.set()
        release.wait(20)

    agent.retire_stale.side_effect = slow_retire
    agent.connection.return_value = ("ws://peer", ("tool",))
    operations = AgentOperations(agent)
    retiring = asyncio.create_task(operations.retire())
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
            assert await operations.connection(()) == ("ws://peer", ("tool",))
    finally:
        release.set()
        await retiring


async def test_two_retirements_run_the_agent_once() -> None:
    release = threading.Event()
    agent = Mock()
    agent.retire_stale.side_effect = lambda: release.wait(20)
    operations = AgentOperations(agent)
    first = asyncio.create_task(operations.retire())
    await asyncio.sleep(0.05)
    await operations.retire()
    release.set()
    await first
    agent.retire_stale.assert_called_once_with()
