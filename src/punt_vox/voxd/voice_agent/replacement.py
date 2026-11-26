"""Build one replacement agent so that failing leaves the account as it was."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent.contracts import Registration
from punt_vox.voxd.voice_agent.disposal import Undeleted
from punt_vox.voxd.voice_agent.fault import ConvAIError, UnknownCreateError

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
                # Held in memory before it is saved, so a failed save still
                # leaves the id in the rollback record.
                record = replace(record, pending_tools=(*record.pending_tools, tool_id))
                self._checkpoint(record, tool_id, "tool", self._disposal.tool)
            agent_id = self._control.create_agent(record.pending_tools, marker)
            record = replace(record, pending_agents=(agent_id,))
            self._checkpoint(record, agent_id, "agent", self._disposal.agent)
            finished = Registration(
                agent_id,
                record.pending_tools,
                fingerprint,
                self._control.credential_id,
                (*base.retired_agents, *self._current_agent(base)),
                (*base.retired_tools, *base.tool_ids),
            )
            self._store.write(finished)
        except Exception as failure:
            try:
                for problem in self._abandon(
                    record, sweep=isinstance(failure, UnknownCreateError)
                ):
                    failure.add_note(problem)
            except (ConvAIError, OSError) as cleanup:
                # Cleanup must never replace the failure that started it.
                failure.add_note(f"cleanup after the failure also failed: {cleanup!r}")
            raise
        return finished, agent_id

    def _abandon(self, record: Registration, *, sweep: bool) -> tuple[str, ...]:
        """Delete what the attempt created and restore the old registration.

        With *sweep*, a create whose reply was lost may have left a resource no
        record names; the account is searched for it too. Returns what the
        caller must still be told: a failed search, or ids left on ElevenLabs
        that no file records.
        """
        agents, tools = record.pending_agents, record.pending_tools
        problems: list[str] = []
        if sweep:
            found_agents, found_tools = self._unrecorded(record, problems)
            agents, tools = (*agents, *found_agents), (*tools, *found_tools)
        kept_agents = Undeleted.ids(self._disposal.agents(agents))
        kept_tools = Undeleted.ids(self._disposal.tools(tools))
        base = self._base
        try:
            if base is None and not (kept_agents or kept_tools):
                self._store.remove()
            else:
                base = base or Registration(None, (), "", self._control.credential_id)
                self._store.write(
                    replace(
                        base,
                        retired_agents=(*base.retired_agents, *kept_agents),
                        retired_tools=(*base.retired_tools, *kept_tools),
                    )
                )
        except OSError as exc:
            # Only what the file already recorded survives for the next
            # retirement; the ids kept here exist nowhere else, so name them.
            logger.warning("Could not restore the voice agent registration: %s", exc)
            if kept_agents or kept_tools:
                problems.append(
                    f"registration not restored ({exc!r}); still on ElevenLabs "
                    f"and unrecorded: agents {list(kept_agents)}, "
                    f"tools {list(kept_tools)}"
                )
        return tuple(problems)

    def _unrecorded(
        self, record: Registration, problems: list[str]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return this host's marked agents and tools that no record names.

        A failed listing is added to *problems* and finds nothing, so the
        other kind is still swept and what it found is still deleted.
        """
        base, listing = self._base, self._control.listing
        found: list[tuple[str, ...]] = []
        for kind, listed, named in (
            (
                "agents",
                listing.marked_agents,
                {*record.agents, *(base.agents if base else ())},
            ),
            (
                "tools",
                listing.marked_tools,
                {*record.tools, *(base.tools if base else ())},
            ),
        ):
            try:
                ids = listed(self._marker.text())
            except ConvAIError as exc:
                problems.append(f"sweep of {kind} failed: {exc}")
                ids = ()
            found.append(tuple(i for i in ids if i not in named))
        return found[0], found[1]

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
