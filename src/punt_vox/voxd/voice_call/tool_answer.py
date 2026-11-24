"""Run one call tool to the JSON the voice agent hears, faults kept path-free."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import TYPE_CHECKING, Self, final

from punt_vox.log_sanitize import SANITIZER
from punt_vox.voxd.voice_call.result_cache import ToolResultCache
from punt_vox.voxd.voice_call.tool_reply import ToolReply
from punt_vox.voxd.wire_fault import SafeFault

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ["ToolAnswer"]

logger = logging.getLogger(__name__)

type Arguments = dict[str, object]
# The cache identity of a request, and the arguments the operation runs with.
type Request = tuple[Arguments, Arguments]


@final
class ToolAnswer:
    """Turn a tool request's outcome into a :class:`ToolReply` for the agent.

    One execution per distinct request is shared through the call's
    :class:`ToolResultCache`. A refused request (a bad field, an unknown
    worker, a tmux failure) answers with its own text: the agent repeats it
    to the person. A daemon-side failure -- the policy file unreadable, not
    UTF-8, or without a closing fence -- answers with :class:`SafeFault`'s
    path-free message; the raw detail, path included, goes to the daemon log
    alone, so the daemon's config path is never spoken.
    """

    __slots__ = ("_cache",)
    _cache: ToolResultCache

    def __new__(cls) -> Self:
        """Create the answerer with a fresh per-call result cache."""
        self = super().__new__(cls)
        self._cache = ToolResultCache()
        return self

    @property
    def replayed(self) -> int:
        """Return how many tool calls were answered from an earlier execution."""
        return self._cache.hits

    async def run(
        self,
        name: str,
        request: Callable[[], Awaitable[Request]],
        operation: Callable[[Arguments], Awaitable[object]],
        *,
        durable: bool,
    ) -> ToolReply:
        """Resolve *request*, run *operation* on it, and answer the outcome.

        Resolution runs inside the same boundary as the operation: reading the
        allowlist to fill a default project can raise the config fault too.
        """
        try:
            identity, resolved = await request()

            async def execute() -> str:
                return json.dumps(await operation(resolved), ensure_ascii=False)

            result = await self._cache.run(
                ToolResultCache.key(name, identity), execute, durable=durable
            )
        except (OSError, UnicodeDecodeError) as exc:
            return self.error(self._fault(name, exc))
        except (
            ValueError,
            KeyError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            return self.error(str(exc))
        return ToolReply.ok(result)

    @staticmethod
    def error(message: str) -> ToolReply:
        """Return *message* as the agent's error answer, kept for the log."""
        return ToolReply.failed(message)

    @staticmethod
    def _fault(name: str, exc: OSError | UnicodeDecodeError) -> str:
        """Log the raw detail host-local and return the message safe to speak."""
        fault = SafeFault.from_exception(exc)
        logger.error(
            "call tool %s failed: %s", name, SANITIZER.escape(fault.log_detail)
        )
        return fault.wire_message
