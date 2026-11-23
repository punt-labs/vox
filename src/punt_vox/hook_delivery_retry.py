"""Retry managed hooks over short daemon outages."""

from __future__ import annotations

import asyncio
from enum import Enum, auto
from typing import TYPE_CHECKING, final

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdHandshakeRejectedError,
    VoxdProtocolError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class HookDeliveryResult(Enum):
    """Distinguish completed delivery from permanent and temporary denial."""

    DELIVERED = auto()
    DENIED = auto()
    PENDING = auto()


@final
class HookDeliveryRetry:
    """Retry transport failures and pending admission within the hook budget."""

    __slots__ = ()

    @classmethod
    async def run(cls, deliver: Callable[[], Awaitable[HookDeliveryResult]]) -> None:
        """Keep the event intact across attempts."""
        async with asyncio.timeout(5):
            for delay in (0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0):
                if await cls._complete(deliver):
                    return
                await asyncio.sleep(delay)
            if await deliver() is HookDeliveryResult.PENDING:
                raise TimeoutError("Worker hook admission pending liveness")

    @classmethod
    async def _complete(
        cls, deliver: Callable[[], Awaitable[HookDeliveryResult]]
    ) -> bool:
        try:
            return await deliver() is not HookDeliveryResult.PENDING
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            if cls._retryable(exc):
                return False
            raise

    @staticmethod
    def _retryable(exc: VoxdConnectionError | VoxdProtocolError) -> bool:
        if isinstance(exc, VoxdProtocolError):
            return isinstance(exc.__cause__, TimeoutError)
        return not isinstance(exc, VoxdHandshakeRejectedError)
