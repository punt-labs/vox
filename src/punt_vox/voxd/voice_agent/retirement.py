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
    from punt_vox.voxd.voice_agent.control import ConvAIControl

__all__ = ["Retirement"]

logger = logging.getLogger(__name__)


@final
class Retirement:
    """Work through the registration's retired ids, one run at a time.

    The ids are read under the registration lock and the deletions merged back
    under it, but the remote deletes run with no lock held, so a slow delete
    never makes a redial wait. A failed delete stays recorded for the next
    call and is never raised.
    """

    __slots__ = ("_control", "_locked", "_read", "_running", "_write")
    _control: ConvAIControl
    _locked: Callable[[], AbstractContextManager[None]]
    _read: Callable[[], Registration | None]
    _write: Callable[[Registration], None]
    _running: threading.Lock  # Held while a run is in flight.

    def __new__(
        cls,
        control: ConvAIControl,
        locked: Callable[[], AbstractContextManager[None]],
        read: Callable[[], Registration | None],
        write: Callable[[Registration], None],
    ) -> Self:
        """Bind the control and the registration's lock, reader and writer."""
        self = super().__new__(cls)
        self._control, self._locked = control, locked
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
            kept_agents = self._kept("agent", agents, self._control.delete_agent)
            kept_tools = self._kept("tool", tools, self._control.delete_tool)
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
        return stored.retired_agents, stored.retired_tools

    def _forget(self, agents: set[str], tools: set[str]) -> None:
        """Drop the deleted ids from the latest registration, keeping newer ones."""
        with self._locked():
            latest = self._read()
            if latest is None:  # A teardown removed the file meanwhile.
                return
            self._write(
                replace(
                    latest,
                    retired_agents=tuple(
                        i for i in latest.retired_agents if i not in agents
                    ),
                    retired_tools=tuple(
                        i for i in latest.retired_tools if i not in tools
                    ),
                )
            )

    @staticmethod
    def _kept(
        kind: str, ids: tuple[str, ...], delete: Callable[[str], None]
    ) -> tuple[str, ...]:
        """Delete each id; return the ones that could not be deleted."""
        kept: list[str] = []
        for resource_id in ids:
            try:
                delete(resource_id)
            except ConvAIError as exc:
                logger.warning("Could not delete old %s %s: %s", kind, resource_id, exc)
                kept.append(resource_id)
        return tuple(kept)
