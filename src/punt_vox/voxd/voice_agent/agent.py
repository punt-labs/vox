"""Keep one persistent ElevenLabs agent and its owned tool IDs in daemon config."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Self, final

from punt_vox.paths import config_dir
from punt_vox.voxd.voice_agent.contracts import Registration
from punt_vox.voxd.voice_agent.control import ConvAIControl
from punt_vox.voxd.voice_agent.disposal import Disposal, Undeleted
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.host_marker import HostMarker
from punt_vox.voxd.voice_agent.registration_file import RegistrationFile
from punt_vox.voxd.voice_agent.replacement import Replacement
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

    __slots__ = ("_clock", "_control", "_disposal", "_marker", "_retirement", "_store")
    _clock: Callable[[], float]
    _control: ConvAIControl
    _store: RegistrationFile
    _disposal: Disposal
    _marker: HostMarker
    _retirement: Retirement

    def __new__(
        cls,
        control: ConvAIControl,
        *,
        path: Path | None = None,  # None selects the daemon's default config path.
        clock: Callable[[], float] = time.monotonic,  # Times the orphan sweep.
    ) -> Self:
        """Bind the agent to the per-user daemon config or a separate test path.

        The default path is the control's: a fake endpoint gets its own
        registration file, so fake and live registrations never meet.
        """
        self = super().__new__(cls)
        self._control = control
        self._clock = clock
        live = config_dir() / "voice-agent.json"
        default = control.registration_path(live)
        where = path if path is not None else default
        self._store = RegistrationFile(where, control.credential_id)
        marker = HostMarker(where)
        self._marker = marker
        self._disposal = Disposal(control, marker)
        self._retirement = Retirement(
            self._disposal, self._store.locked, self._store.read, self._store.write
        )
        return self

    def connection(self, tools: tuple[ClientTool, ...]) -> tuple[str, tuple[str, ...]]:
        """Return a fresh signed URL and the registered IDs for the handshake."""
        ordered = tuple(sorted(tools, key=lambda tool: tool.name))
        fingerprint = self._control.registration_fingerprint(ordered)
        with self._store.locked():
            stored = self._store.read()
            reusable = (
                None if stored is None else self._reusable_id(stored, fingerprint)
            )
            if stored is not None and reusable is not None:
                agent_id, tool_ids = reusable, stored.tool_ids
            else:
                # A changed spec replaces the agent; the old resources are
                # deleted after the call connects, never in its way.
                replacement = Replacement(
                    self._control,
                    self._store,
                    self._disposal,
                    self._marker,
                    stored,
                )
                record, agent_id = replacement.build(ordered, fingerprint, self._clock)
                tool_ids = record.tool_ids
        return self._control.signed_url(agent_id), tool_ids

    @staticmethod
    def _reusable_id(stored: Registration, fingerprint: str) -> str | None:
        """Return the agent id if the registration serves this spec, else None."""
        if stored.configuration_hash != fingerprint:
            return None
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

    def _delete(self, stored: Registration) -> None:
        """Delete what can be deleted; keep and report what cannot."""
        agents_left = self._disposal.agents(stored.agents)
        self._store.write(stored.remaining(Undeleted.ids(agents_left), stored.tools))
        tools_left = self._disposal.tools(stored.tools)
        if not (left := (*agents_left, *tools_left)):
            self._store.remove()
            return
        self._store.write(
            stored.remaining(Undeleted.ids(agents_left), Undeleted.ids(tools_left))
        )
        reasons = "; ".join(item.reason for item in left)
        msg = f"Left in place on ElevenLabs, still recorded: {reasons}"
        raise ConvAIError(msg)
