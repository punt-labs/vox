"""Build one replacement agent so that failing leaves the account as it was."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent.contracts import Registration
from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.voice_agent.contracts import ClientTool
    from punt_vox.voxd.voice_agent.control import ConvAIControl
    from punt_vox.voxd.voice_agent.disposal import Disposal
    from punt_vox.voxd.voice_agent.host_marker import HostMarker
    from punt_vox.voxd.voice_agent.registration_file import RegistrationFile

__all__ = ["Replacement"]

logger = logging.getLogger(__name__)


@final
class Replacement:
    """Create the tools and agent for one changed spec, under the registration lock.

    The registration file names the intent before the first remote create and
    each created id as it appears, so a run that dies is cleaned up by the
    next retirement. The old registration stays current until the new agent is
    recorded; any failure before that deletes what this attempt created.
    """

    __slots__ = ("_base", "_control", "_disposal", "_marker", "_store")
    _base: Registration | None  # None before the first provisioning.
    _control: ConvAIControl
    _disposal: Disposal
    _marker: HostMarker
    _store: RegistrationFile

    def __new__(
        cls,
        control: ConvAIControl,
        store: RegistrationFile,
        disposal: Disposal,
        marker: HostMarker,
        stored: Registration | None,  # None: nothing has been provisioned yet.
    ) -> Self:
        """Bind the registration being replaced, with a dead run's ids retired."""
        self = super().__new__(cls)
        self._control, self._store = control, store
        self._disposal, self._marker = disposal, marker
        self._base = None if stored is None else cls._without_pending(stored)
        return self

    def build(
        self, tools: tuple[ClientTool, ...], fingerprint: str
    ) -> tuple[Registration, str]:
        """Create the tools and agent; return the new record and the agent id."""
        base = self._base or Registration(None, (), "", self._control.credential_id)
        record = base
        try:
            self._store.write(record)  # The intent, before anything is created.
            marker = self._marker.text()
            for tool in tools:
                tool_id = self._control.create_tool(tool, marker)
                grown = replace(record, pending_tools=(*record.pending_tools, tool_id))
                self._checkpoint(grown, tool_id, "tool", self._disposal.tool)
                record = grown
            agent_id = self._control.create_agent(record.pending_tools, marker)
            grown = replace(record, pending_agents=(agent_id,))
            self._checkpoint(grown, agent_id, "agent", self._disposal.agent)
            record = grown
            finished = Registration(
                agent_id,
                record.pending_tools,
                fingerprint,
                self._control.credential_id,
                (*base.retired_agents, *self._current_agent(base)),
                (*base.retired_tools, *base.tool_ids),
            )
            self._store.write(finished)
        except Exception:
            self._abandon(record)
            raise
        return finished, agent_id

    def _abandon(self, record: Registration) -> None:
        """Delete what the attempt created and restore the old registration."""
        kept_agents = self._disposal.agents(record.pending_agents)
        kept_tools = self._disposal.tools(record.pending_tools)
        base = self._base
        try:
            if base is None and not (kept_agents or kept_tools):
                self._store.remove()
                return
            base = base or Registration(None, (), "", self._control.credential_id)
            self._store.write(
                replace(
                    base,
                    retired_agents=(*base.retired_agents, *kept_agents),
                    retired_tools=(*base.retired_tools, *kept_tools),
                )
            )
        except OSError as exc:
            # The intent on disk still names what the file recorded, so the
            # next retirement finishes the job.
            logger.warning("Could not restore the voice agent registration: %s", exc)

    def _checkpoint(
        self,
        record: Registration,
        resource_id: str,
        kind: str,
        delete: Callable[[str], None],
    ) -> None:
        try:
            self._store.write(record)
        except OSError as write_error:
            try:
                delete(resource_id)
            except ConvAIError as delete_error:
                msg = (
                    f"Could not record or delete the ElevenLabs {kind} {resource_id}; "
                    "delete it manually in the workspace used to provision it"
                )
                raise ConvAIError(msg) from ExceptionGroup(
                    msg, [write_error, delete_error]
                )
            raise

    @staticmethod
    def _current_agent(record: Registration) -> tuple[str, ...]:
        return () if record.agent_id is None else (record.agent_id,)

    @staticmethod
    def _without_pending(stored: Registration) -> Registration:
        """Return *stored* with a dead run's pending ids moved to retired."""
        return replace(
            stored,
            retired_agents=stored.orphaned_agents,
            retired_tools=stored.orphaned_tools,
            pending_agents=(),
            pending_tools=(),
        )
