"""Answer a re-asked tool call from the first answer instead of doing it twice."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Final, Self, final

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ["ToolResultCache"]


# Sentence punctuation a transcript may put around a word; signs stay in.
_EDGES: Final = ".,!?;:'\"()\u201c\u201d\u2018\u2019"
# Launch tasks may be transcribed afresh; steering words stay exact.
_SPOKEN: Final = frozenset({"task"})


@final
class ToolResultCache:
    """Share one execution per tool and arguments for the life of a call.

    A barge-in makes ElevenLabs abandon a pending tool call and ask again once
    the person finishes speaking. A re-ask joins the execution still running,
    or returns its stored result, so a worker is never started twice and the
    agent hears back at once. Reads (``durable=False``) only share an execution
    in flight, because a later status question deserves a fresh answer.
    """

    __slots__ = ("_hits", "_running", "_settled")
    _running: dict[str, asyncio.Task[str]]
    _settled: dict[str, str]
    _hits: int

    def __new__(cls) -> Self:
        """Create an empty cache for one call."""
        self = super().__new__(cls)
        self._running = {}
        self._settled = {}
        self._hits = 0
        return self

    @property
    def hits(self) -> int:
        """Return how many calls were answered without a new execution."""
        return self._hits

    @staticmethod
    def key(name: str, arguments: dict[str, object]) -> str:
        """Return a canonical key that argument order and spoken punctuation share.

        A re-ask after barge-in is transcribed afresh, so a task of "Hello,
        world!" and one of "hello world." must name the same request, while
        "-5" and "5" stay different instructions.
        """
        canonical = {
            field: ToolResultCache._spoken(value)
            if field in _SPOKEN and isinstance(value, str)
            else value
            for field, value in arguments.items()
        }
        return json.dumps([name, canonical], sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _spoken(text: str) -> str:
        words = (word.strip(_EDGES) for word in text.casefold().split())
        return " ".join(word for word in words if word)

    async def run(
        self, key: str, execute: Callable[[], Awaitable[str]], *, durable: bool
    ) -> str:
        """Return the shared result for ``key``, executing at most once at a time."""
        if key in self._settled:
            self._hits += 1
            return self._settled[key]
        task = self._running.get(key)
        if task is None:
            task = asyncio.ensure_future(execute())
            self._running[key] = task
            task.add_done_callback(
                lambda done: self._settle(key, done, durable=durable)
            )
        else:
            self._hits += 1
        # Shielded: a caller cancelled by its own call ending must not cancel the
        # execution a re-ask is waiting on.
        return await asyncio.shield(task)

    def _settle(self, key: str, task: asyncio.Task[str], *, durable: bool) -> None:
        if self._running.get(key) is task:
            del self._running[key]
        if durable and not task.cancelled() and task.exception() is None:
            self._settled[key] = task.result()
