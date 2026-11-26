from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING
from unittest.mock import patch

from punt_vox.commands.voice_call_follow import CallFollower

if TYPE_CHECKING:
    from collections.abc import Callable


def test_the_event_loop_is_closed_with_sigint_ignored() -> None:
    """A second Ctrl-C during loop shutdown must not abandon its cleanup."""
    seen: list[Callable[[int, object], object] | int | None] = []
    real_close = asyncio.Runner.close

    def close(self: asyncio.Runner) -> None:
        seen.append(signal.getsignal(signal.SIGINT))
        real_close(self)

    async def nothing() -> int:
        return 1

    with patch.object(asyncio.Runner, "close", close):
        assert CallFollower.run(nothing()) == 1
    assert seen == [signal.SIG_IGN]


def test_sigint_is_restored_after_the_loop_closes() -> None:
    before = signal.getsignal(signal.SIGINT)

    async def nothing() -> None:
        return None

    CallFollower.run(nothing())
    assert signal.getsignal(signal.SIGINT) == before
