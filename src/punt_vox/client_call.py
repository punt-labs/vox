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

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from punt_vox.client import VoxClient
    from punt_vox.client_errors import VoxError

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

        Running out of the bound raises ``VoxdProtocolError``; any other
        ``TimeoutError`` (a websockets open timeout, say) raises
        ``VoxdConnectionError`` naming its own cause.
        """
        # asyncio.timeout(None) never expires, so an unbounded call shares
        # this path; the deadline is fixed here, before connect starts.
        bound = asyncio.timeout(self._within_s)
        try:
            async with bound:
                await client.connect()
                return await op(client)
        except TimeoutError as exc:
            raise self._timed_out(bound, exc) from exc
        finally:
            # The answer, or the failure, is already decided. A close the
            # deadline cuts short only abandons a socket the event loop tears
            # down when this call's loop ends.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout_at(bound.when()):
                    await client.close()

    def _timed_out(self, bound: asyncio.Timeout, exc: TimeoutError) -> VoxError:
        """Return the error for a ``TimeoutError`` that ended the call.

        Only this call's own bound running out is "no answer within N s"; a
        timeout raised inside the call before then is reported as itself.
        """
        if bound.expired():
            return VoxdProtocolError(f"no answer from voxd within {self._within_s}s")
        return VoxdConnectionError(f"voxd request timed out ({exc!r})")
