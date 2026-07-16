"""Apply a vibe change and, while music plays, hint the agent to re-pool it.

The ``vibe`` MCP tool is a thin adapter over :class:`VibeCommand`. The command
mutates the session, persists the change to config, and -- reading the daemon's
Program status *read-only* -- enriches the reply with a :class:`MusicHint` when a
Program is playing. It never posts a music or switch signal: the hint is the
whole coupling, so the vibe/music layering stays clean (PL-MD-1).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol, Self, final

from websockets.exceptions import WebSocketException

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.config import ConfigStore
from punt_vox.music_hint import MusicHint
from punt_vox.program_gateway import ProgramGateway
from punt_vox.types_programs.status import ProgramStatus
from punt_vox.vibe import VibeChange

__all__ = ["VibeCommand", "VibeSession"]

logger = logging.getLogger(__name__)

_TRACE = "[vibe-trace]"
_DAEMON_ERRORS = (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError)


class VibeSession(Protocol):
    """The session surface :class:`VibeCommand` reads and mutates.

    A structural interface (PY-TS-6) so the command never imports the concrete
    ``SessionConfig`` that lives in the presentation layer -- the dependency arrow
    keeps pointing inward.
    """

    @property
    def vibe(self) -> str | None:
        """Return the current mood, or ``None`` when it is cleared."""
        ...

    @property
    def vibe_mode(self) -> str:
        """Return the vibe detection mode ('auto', 'manual', or 'off')."""
        ...

    @property
    def style(self) -> str | None:
        """Return the last authored music style, or ``None`` when unset."""
        ...

    def change_vibe(self, change: VibeChange) -> dict[str, str]:
        """Apply *change*, mutate the session, and return the persisted updates."""
        ...


@final
class VibeCommand:
    """Apply a vibe change to the session and enrich the reply with a music hint."""

    __slots__ = ("_config_dir", "_gateway", "_session")

    _session: VibeSession
    _gateway: ProgramGateway
    _config_dir: Path | None

    def __new__(
        cls, session: VibeSession, gateway: ProgramGateway, config_dir: Path | None
    ) -> Self:
        self = super().__new__(cls)
        self._session = session
        self._gateway = gateway
        self._config_dir = config_dir
        return self

    def apply(self, mood: str | None, tags: str | None, mode: str | None) -> str:
        """Return the JSON reply for the vibe change: updates plus an optional hint."""
        try:
            updates = self._session.change_vibe(
                VibeChange(mood=mood, tags=tags, mode=mode)
            )
        except ValueError:
            return _error(f"Invalid mode '{mode}'. Use auto/manual/off.")
        if not updates:
            return _error("Provide at least one of: mood, tags, mode.")
        try:
            ConfigStore(self._config_dir).write_fields(updates)
        except ValueError as exc:  # mood/tags carrying a newline or double-quote
            return _error(str(exc))

        payload: dict[str, object] = {"vibe": updates}
        self._enrich_with_music(payload)
        return json.dumps(payload)

    def _enrich_with_music(self, payload: dict[str, object]) -> None:
        """Add a music hint when a Program plays; always emit the vibe-set trace.

        Reading the status is read-only observation -- never a control signal. A
        daemon that is down fails safe to "no hint": the mood still persisted, and
        a Program retune is never a side effect of setting the session mood.
        """
        status = self._read_status()
        hint = (
            None
            if status is None
            else MusicHint.for_status(status, self._session.vibe, self._session.style)
        )
        if hint is not None:
            payload["music"] = hint.music_state()
            payload["music_hint"] = hint.directive
        # music_playing mirrors the audible gate: a hint fires iff genuinely playing.
        playing = hint is not None
        logger.info(
            "%s vibe set mood=%s mode=%s music_playing=%s style=%s hint=%s",
            _TRACE,
            self._session.vibe or "-",
            self._session.vibe_mode,
            str(playing).lower(),
            self._session.style or "-",
            "emitted" if hint is not None else "none",
        )

    def _read_status(self) -> ProgramStatus | None:
        """Return the daemon's Program status, or ``None`` when it is unreachable."""
        try:
            return self._gateway.status()
        except _DAEMON_ERRORS:
            logger.warning("%s vibe set: status unavailable, no hint", _TRACE)
            return None


def _error(message: str) -> str:
    """Return a JSON error string."""
    return json.dumps({"error": message})
