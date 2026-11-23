"""Retry managed hooks over short daemon outages."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, final

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdHandshakeRejectedError,
    VoxdProtocolError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class HookAdmissionPending(VoxdProtocolError):
    """The registered worker is still awaiting a positive liveness probe."""


@final
class HookDeliveryRetry:
    """Retry transport failures and pending admission within the hook budget."""

    __slots__ = ()

    @classmethod
    async def run(cls, deliver: Callable[[], Awaitable[None]]) -> None:
        """Keep the event intact across attempts."""
        async with asyncio.timeout(5):
            for delay in (0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0, None):
                try:
                    await deliver()
                except (VoxdConnectionError, VoxdProtocolError) as exc:
                    if delay is None or not cls._retryable(exc):
                        raise
                    await asyncio.sleep(delay)
                else:
                    return

    @staticmethod
    def _retryable(exc: VoxdConnectionError | VoxdProtocolError) -> bool:
        if isinstance(exc, HookAdmissionPending):
            return True
        if isinstance(exc, VoxdProtocolError):
            return isinstance(exc.__cause__, TimeoutError)
        return not isinstance(exc, VoxdHandshakeRejectedError)
