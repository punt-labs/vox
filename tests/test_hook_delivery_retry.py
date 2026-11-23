from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from punt_vox.client_errors import VoxdHandshakeRejectedError, VoxdRefusedError
from punt_vox.hook_delivery_retry import HookDeliveryRetry


async def test_retries_transient_refusal_then_stops_on_handshake_rejection() -> None:
    deliver = AsyncMock(
        side_effect=[VoxdRefusedError("restarting"), VoxdHandshakeRejectedError("auth")]
    )

    with (
        patch("asyncio.sleep", new_callable=AsyncMock) as delay,
        pytest.raises(VoxdHandshakeRejectedError, match="auth"),
    ):
        await HookDeliveryRetry.run(deliver)

    assert deliver.await_count == 2
    delay.assert_awaited_once_with(0.1)
