"""The production :class:`ProgramGateway` -- a WebSocket adapter over ``voxd``.

``ClientProgramGateway`` is the only place the client surfaces meet the wire: it
translates each gateway call into a session-free ``program_*`` message through a
:class:`VoxClientSync`, and parses the daemon's reply back into the domain value
objects the surfaces render (:class:`ProgramStatus`, :class:`CommandOutcome`,
:class:`ProgramSummary`). It holds no policy of its own -- authoring, rendering,
and error presentation stay in the surfaces; the daemon owns the state.
"""

from __future__ import annotations

from typing import Any, Self, final

from punt_vox.client_sync import VoxClientSync
from punt_vox.program_control import (
    CommandOutcome,
    ProgramSummary,
    SelectionRequest,
    StartRequest,
)
from punt_vox.voxd.programs.status import ProgramStatus
from punt_vox.voxd.programs.wire import JsonObject

__all__ = ["ClientProgramGateway"]


@final
class ClientProgramGateway:
    """Back the ``ProgramGateway`` seam with WebSocket calls to ``voxd``."""

    __slots__ = ("_client",)
    _client: VoxClientSync

    def __new__(cls, client: VoxClientSync) -> Self:
        self = super().__new__(cls)
        self._client = client
        return self

    def status(self) -> ProgramStatus:
        """Return the daemon's authoritative Program status, parsed from the wire."""
        resp = self._client.program_status()
        obj = JsonObject.coerce(resp, "program_status")
        return ProgramStatus.from_wire(obj.require_object("status"))

    def start(self, request: StartRequest) -> CommandOutcome:
        """Turn a Program on from the authored ``request`` (carrying the vibe)."""
        resp = self._client.program_on(
            style=request.style,
            vibe=request.vibe,
            name=request.name,
            prompts=request.prompts,
        )
        return self._outcome(resp)

    def stop(self) -> CommandOutcome:
        """Turn the active Program off."""
        return self._outcome(self._client.program_off())

    def advance(self) -> CommandOutcome:
        """Advance to another Part."""
        return self._outcome(self._client.program_next())

    def select(self, request: SelectionRequest) -> CommandOutcome:
        """Replay a Selection resolved by id (direct) or by tags (F#7)."""
        return self._outcome(
            self._client.program_select(
                style=request.style,
                vibe=request.vibe,
                name=request.name,
                album_id=request.id,
            )
        )

    def catalog(self) -> tuple[ProgramSummary, ...]:
        """Return every album, parsed from the ``program_list`` reply."""
        obj = JsonObject.coerce(self._client.program_list(), "program_list")
        return tuple(
            self._summary(JsonObject.coerce(item, "program_list.programs"))
            for item in obj.require_list("programs")
        )

    @staticmethod
    def _outcome(resp: dict[str, Any]) -> CommandOutcome:
        """Read the applied/rejected result (design F7) from a command reply.

        A reply omitting ``applied`` is treated as applied -- the daemon only
        writes ``applied: false`` to flag a lost race (PY-EH-8: absence is the
        documented "it went through" contract). A rejection is guaranteed a
        non-empty ``message`` so the surfaces never render a blank line for a
        refused command (finding F4/F7); an applied command may carry none.
        """
        obj = JsonObject.coerce(resp, "command")
        applied = obj.opt_bool("applied") is not False
        message = obj.opt_str("message") or ("" if applied else "command rejected")
        return CommandOutcome(applied=applied, message=message)

    @staticmethod
    def _summary(obj: JsonObject) -> ProgramSummary:
        """Parse one catalogue entry (id, tags, counts) from the wire."""
        return ProgramSummary(
            id=obj.require_str("id"),
            style=obj.require_str("style"),
            vibe=obj.require_str("vibe"),
            format=obj.require_str("format"),
            ready=obj.require_int("ready"),
            total=obj.opt_int("total") or 0,
            name=obj.opt_str("name"),
        )
