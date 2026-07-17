"""Apply a vibe change and, while music plays, hint the agent to re-pool it.

The ``vibe`` MCP tool is a thin adapter over :class:`VibeCommand`. The command
mutates the session, persists the change to config, and -- reading the daemon's
Program status *read-only* -- enriches the reply with a :class:`MusicHint` when a
Program is playing. It never posts a music or switch signal: the hint is the
whole coupling, so the vibe/music layering stays clean (PL-MD-1).

The style the hint names comes from :class:`MusicPreference`, a session register
the music tools keep current on every playback change so a stale style never
names the wrong genre.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self, final

from websockets.exceptions import WebSocketException

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.config import ConfigStore
from punt_vox.music_hint import MusicHint
from punt_vox.program_gateway import ProgramGateway
from punt_vox.vibe import VibeChange
from punt_vox.vibe_trace import VibeTraceLog

if TYPE_CHECKING:
    # Annotation-only: the daemon Program types are named in signatures but never
    # constructed or type-tested here, so they carry no runtime dependency.
    from punt_vox.types_programs import CommandOutcome, ProgramStatus

__all__ = ["MusicPreference", "VibeCommand", "VibeSession"]

_DAEMON_ERRORS = (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError)


@final
class MusicPreference:
    """The genre the agent last set music to -- the style the re-pool hint names.

    A session register held apart from the vibe cluster so a stale style never
    names the wrong genre: every playback change updates it. A start adopts a
    named style (or keeps the current one, matching the daemon's style-persist);
    a replay adopts the selection's style (or clears for a style-less union
    radio); a stop clears it.
    """

    __slots__ = ("_style", "_trace")

    _style: str | None
    _trace: VibeTraceLog

    def __new__(cls, style: str | None = None) -> Self:
        self = super().__new__(cls)
        self._style = style
        self._trace = VibeTraceLog.default()
        return self

    @property
    def style(self) -> str | None:
        """Return the current music style, or ``None`` when off or style-less."""
        return self._style

    def started(self, style: str | None) -> None:
        """Record a ``music on``: adopt *style*, or keep the current one if omitted."""
        if style is not None:
            self._style = style

    def selected(self, style: str | None) -> None:
        """Record a ``music play``: adopt the selection's style, else clear it."""
        self._style = style

    def stopped(self) -> None:
        """Record a ``music off``: no style is playing."""
        self._style = None

    def confirm_started(
        self,
        outcome: CommandOutcome,
        style: str | None,
        vibe: str | None,
        *,
        authored: bool,
    ) -> None:
        """Adopt *style* and log the proof only when the daemon applied the start.

        The register and its trace turn solely on ``outcome.applied``, so a
        rejected/lost-race start leaves the genre untouched and never claims a
        false re-pool -- the one place that decision lives.
        """
        if not outcome.applied:
            return
        self.started(style)
        # Name the EFFECTIVE style: persisted when the arg was omitted.
        self._trace.record(
            f"music on style={self._style or '-'} vibe={vibe or '-'} "
            f"prompts={'authored' if authored else 'fallback'}"
        )

    def confirm_selected(
        self,
        outcome: CommandOutcome,
        style: str | None,
        vibe: str | None,
        name: str | None,
    ) -> None:
        """Adopt the replay's style and log the proof only on an applied replay."""
        if not outcome.applied:
            return
        self.selected(style)
        self._trace.record(
            f"music play style={style or '-'} vibe={vibe or '-'} name={name or '-'}"
        )

    def confirm_stopped(self, outcome: CommandOutcome) -> None:
        """Clear the style and log the proof only when the daemon applied the stop."""
        if not outcome.applied:
            return
        self.stopped()
        self._trace.record("music off")


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

    def change_vibe(self, change: VibeChange) -> dict[str, str]:
        """Apply *change*, mutate the session, and return the persisted updates."""
        ...


@final
class VibeCommand:
    """Apply a vibe change to the session and enrich the reply with a music hint."""

    __slots__ = ("_config_dir", "_gateway", "_pref", "_session", "_trace")

    _session: VibeSession
    _gateway: ProgramGateway
    _config_dir: Path | None
    _pref: MusicPreference
    _trace: VibeTraceLog

    def __new__(
        cls,
        session: VibeSession,
        gateway: ProgramGateway,
        config_dir: Path | None,
        pref: MusicPreference,
    ) -> Self:
        self = super().__new__(cls)
        self._session = session
        self._gateway = gateway
        self._config_dir = config_dir
        self._pref = pref
        self._trace = VibeTraceLog.default()
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
        style = self._pref.style
        hint = (
            None
            if status is None
            else MusicHint.for_status(status, self._session.vibe, style)
        )
        if hint is not None:
            payload["music"] = hint.music_state()
            payload["music_hint"] = hint.directive
        # music_playing is a truthful tri-state: "unknown" when the status read
        # failed (never a false "false" masking a daemon outage as music-off), else
        # the audible gate; hint_emitted stays false unless a known style is playing.
        music_playing = "unknown" if status is None else str(status.is_playing).lower()
        self._trace.record(
            f"vibe set mood={self._session.vibe or '-'} "
            f"mode={self._session.vibe_mode} music_playing={music_playing} "
            f"hint_emitted={str(hint is not None).lower()} style={style or '-'}"
        )

    def _read_status(self) -> ProgramStatus | None:
        """Return the daemon's Program status, or ``None`` when it is unreachable."""
        try:
            return self._gateway.status()
        except _DAEMON_ERRORS as exc:
            self._trace.record(f"vibe set: status unavailable, no hint: {exc}")
            return None


def _error(message: str) -> str:
    """Return a JSON error string."""
    return json.dumps({"error": message})
