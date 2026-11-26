"""Keep one persistent ElevenLabs agent and its owned tool IDs in daemon config."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.paths import config_dir
from punt_vox.voxd.voice_agent.contracts import Registration
from punt_vox.voxd.voice_agent.control import ConvAIControl
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.registration_file import RegistrationFile
from punt_vox.voxd.voice_agent.retirement import Retirement

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from punt_vox.voxd.voice_agent.contracts import ClientTool

__all__ = ["VoiceAgent"]

logger = logging.getLogger(__name__)


@final
class VoiceAgent:
    """Provision lazily and reuse one account-bound agent across conversations.

    Call the blocking control operations off the daemon's event loop. Register
    a stable tool catalog; explicitly tear the agent down to replace that catalog.
    """

    __slots__ = ("_control", "_retirement", "_store")
    _control: ConvAIControl
    _store: RegistrationFile
    _retirement: Retirement

    def __new__(
        cls,
        control: ConvAIControl,
        *,
        path: Path | None = None,  # None selects the daemon's default config path.
    ) -> Self:
        """Bind the agent to the per-user daemon config or a separate test path.

        The default path is the control's: a fake endpoint gets its own
        registration file, so fake and live registrations never meet.
        """
        self = super().__new__(cls)
        self._control = control
        live = config_dir() / "voice-agent.json"
        default = control.registration_path(live)
        self._store = RegistrationFile(
            path if path is not None else default, control.credential_id
        )
        self._retirement = Retirement(
            control, self._store.locked, self._store.read, self._store.write
        )
        return self

    def connection(self, tools: tuple[ClientTool, ...]) -> tuple[str, tuple[str, ...]]:
        """Return a fresh signed URL and the registered IDs for the handshake."""
        ordered = tuple(sorted(tools, key=lambda tool: tool.name))
        fingerprint = self._control.registration_fingerprint(ordered)
        with self._store.locked():
            stored = self._store.read()
            retired = Registration(None, (), "", "")
            if stored is not None and self._is_stale(stored, fingerprint):
                # A changed spec replaces the agent; the old resources are
                # deleted after the call connects, never in its way.
                retired, stored = stored, None
            if stored is None:
                stored = self._provision(ordered, fingerprint, retired)
            agent_id = self._usable_agent_id(stored)
        return self._control.signed_url(agent_id), stored.tool_ids

    @staticmethod
    def _is_stale(stored: Registration, fingerprint: str) -> bool:
        """Return whether the registration cannot serve this spec and must go."""
        return stored.agent_id is None or stored.configuration_hash != fingerprint

    @staticmethod
    def _usable_agent_id(stored: Registration) -> str:
        """Return the provisioned agent id, or raise."""
        if stored.agent_id is None:
            msg = "No provisioned ElevenLabs agent"
            raise ConvAIError(msg)
        return stored.agent_id

    def retire_stale(self) -> None:
        """Delete the replaced agents and tools after a call; never raises."""
        self._retirement.run()

    def teardown(self) -> None:
        """Delete managed resources; retain the config if deletion must be retried.

        The registration file goes under the lock; the lock file itself
        stays, for a fake endpoint as for the live one. Unlinking a flock
        path is unsafe: a waiter that already opened the old inode would
        hold a lock on it while a newcomer created a fresh file at the same
        path, and both would believe they held the lock. One small lock
        file per fake port is the price of that safety.
        """
        with self._store.locked():
            if (stored := self._store.read(for_teardown=True)) is not None:
                self._delete(stored)

    def _provision(
        self,
        tools: tuple[ClientTool, ...],
        fingerprint: str,
        replaced: Registration,
    ) -> Registration:
        record = Registration(
            None,
            (),
            fingerprint,
            self._control.credential_id,
            (
                *replaced.retired_agents,
                *(() if replaced.agent_id is None else (replaced.agent_id,)),
            ),
            (*replaced.retired_tools, *replaced.tool_ids),
        )
        self._store.write(record)
        for tool in tools:
            tool_id = self._control.create_tool(tool)
            record = replace(record, tool_ids=(*record.tool_ids, tool_id))
            self._checkpoint(record, tool_id, "tool", self._control.delete_tool)
        agent_id = self._control.create_agent(record.tool_ids)
        record = replace(record, agent_id=agent_id)
        self._checkpoint(record, agent_id, "agent", self._control.delete_agent)
        return record

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

    def _delete(self, stored: Registration) -> None:
        agents = (
            *stored.retired_agents,
            *(() if stored.agent_id is None else (stored.agent_id,)),
        )
        for agent_id in agents:
            self._control.delete_agent(agent_id)
        self._store.write(replace(stored, agent_id=None, retired_agents=()))
        for tool_id in (*stored.retired_tools, *stored.tool_ids):
            self._control.delete_tool(tool_id)
        self._store.remove()
