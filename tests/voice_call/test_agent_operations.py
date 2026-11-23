from __future__ import annotations

import asyncio
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
