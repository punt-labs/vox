"""One request to voxd on a fresh connection, optionally bounded end to end.

``VoxClientSync`` opens a connection per call. :class:`BoundedCall` owns that
connection's lifecycle -- connect, run one operation, close -- and, when given
a bound, holds the whole of it to that many seconds. A bounded poll (the
daemon lifecycle's health wait) passes what is left of its wait, so a voxd
that accepts the connection but never answers cannot carry the caller past
its deadline.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Self, final

from punt_vox.client_errors import VoxdProtocolError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from punt_vox.client import VoxClient

__all__ = ["BoundedCall"]


@final
class BoundedCall:
    """Connect, run one operation, close -- within an optional time bound."""

    __slots__ = ("_within_s",)

    _within_s: float | None  # None: no overall bound; per-step timeouts apply

    def __new__(cls, within_s: float | None = None) -> Self:
        self = super().__new__(cls)
        self._within_s = within_s
        return self

    async def run[T](
        self, client: VoxClient, op: Callable[[VoxClient], Coroutine[Any, Any, T]]
    ) -> T:
        """Return *op*'s result on *client*, closing the connection after.

        Running out of the bound raises ``VoxdProtocolError``.
        """
        deadline = self._deadline()
        try:
            async with asyncio.timeout_at(deadline):
                await client.connect()
                return await op(client)
        except TimeoutError as exc:
            msg = f"no answer from voxd within {self._within_s}s"
            raise VoxdProtocolError(msg) from exc
        finally:
            # The answer, or the failure, is already decided. A close the
            # deadline cuts short only abandons a socket the event loop tears
            # down when this call's loop ends.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout_at(deadline):
                    await client.close()

    def _deadline(self) -> float | None:
        """Return the loop time the call must end by; ``None`` when unbounded."""
        if self._within_s is None:
            return None
        return asyncio.get_running_loop().time() + self._within_s
