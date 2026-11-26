"""Delete replaced ElevenLabs resources without holding a lock across the calls."""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from punt_vox.voxd.voice_agent.contracts import Registration
    from punt_vox.voxd.voice_agent.disposal import Disposal

__all__ = ["Retirement"]

logger = logging.getLogger(__name__)


@final
class Retirement:
    """Work through the registration's orphaned ids, one run at a time.

    Orphaned means retired, or left pending by a replacement that died: a live
    one holds the registration lock for its whole run, so it never shows here.

    The ids are read under the registration lock and the deletions merged back
    under it, but the remote deletes run with no lock held, so a slow delete
    never makes a redial wait. A failed delete stays recorded for the next
    call and is never raised.
    """

    __slots__ = ("_disposal", "_locked", "_read", "_running", "_write")
    _disposal: Disposal
    _locked: Callable[[], AbstractContextManager[None]]
    _read: Callable[[], Registration | None]
    _write: Callable[[Registration], None]
    _running: threading.Lock  # Held while a run is in flight.

    def __new__(
        cls,
        disposal: Disposal,
        locked: Callable[[], AbstractContextManager[None]],
        read: Callable[[], Registration | None],
        write: Callable[[Registration], None],
    ) -> Self:
        """Bind the disposal and the registration's lock, reader and writer."""
        self = super().__new__(cls)
        self._disposal, self._locked = disposal, locked
        self._read, self._write = read, write
        self._running = threading.Lock()
        return self

    def run(self) -> None:
        """Delete what is retired and forget what was deleted; log, never raise."""
        if not self._running.acquire(blocking=False):
            return  # Another run is already working through the list.
        try:
            agents, tools = self._retired_ids()
            if not (agents or tools):
                return
            kept_agents = self._disposal.agents(agents)
            kept_tools = self._disposal.tools(tools)
            self._forget({*agents} - {*kept_agents}, {*tools} - {*kept_tools})
        except (ConvAIError, OSError) as exc:
            logger.warning("Old ElevenLabs resources not retired this time: %s", exc)
        finally:
            self._running.release()

    def _retired_ids(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        with self._locked():
            stored = self._read()
        if stored is None:
            return (), ()
        return stored.orphaned_agents, stored.orphaned_tools

    def _forget(self, agents: set[str], tools: set[str]) -> None:
        """Drop the deleted ids from the latest registration, keeping newer ones."""
        with self._locked():
            latest = self._read()
            if latest is None:  # A teardown removed the file meanwhile.
                return
            self._write(
                replace(
                    latest,
                    retired_agents=self._left(latest.retired_agents, agents),
                    retired_tools=self._left(latest.retired_tools, tools),
                    pending_agents=self._left(latest.pending_agents, agents),
                    pending_tools=self._left(latest.pending_tools, tools),
                )
            )

    @staticmethod
    def _left(ids: tuple[str, ...], deleted: set[str]) -> tuple[str, ...]:
        return tuple(i for i in ids if i not in deleted)
