"""Decide whether a consequential tool request waits for the person's yes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_call.confirmation_gate import ConfirmationGate
from punt_vox.voxd.voice_call.readback import ReadBack
from punt_vox.voxd.voice_call.tool_fields import ToolFields

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.contracts import ProjectAllowlist
    from punt_vox.voxd.voice_call.provenance import CallProvenance
    from punt_vox.voxd.voice_call.tool_answer import Guard
    from punt_vox.voxd.voice_call.worker_directory import WorkerDirectory

__all__ = ["RequestGuard"]


@final
class RequestGuard:
    """Hold a tainted call's request, unless it is a bare denial of a live prompt."""

    __slots__ = ("_gate", "_projects", "_provenance", "_readback")
    _provenance: CallProvenance
    _gate: ConfirmationGate
    _readback: ReadBack
    _projects: ProjectAllowlist

    def __new__(
        cls,
        provenance: CallProvenance,
        directory: WorkerDirectory,
        projects: ProjectAllowlist,
    ) -> Self:
        """Bind the call's taint record, worker directory and repository allowlist."""
        self = super().__new__(cls)
        self._provenance = provenance
        self._gate = ConfirmationGate(provenance)
        self._readback = ReadBack(directory)
        self._projects = projects
        return self

    @property
    def provenance(self) -> CallProvenance:
        """Return the record of untrusted text in the call."""
        return self._provenance

    @property
    def gate(self) -> ConfirmationGate:
        """Return the gate the call feeds with what was said."""
        return self._gate

    def for_tool(self, name: str) -> Guard:
        """Return the guard the answerer runs, before its cache, for ``name``."""

        async def hold(key: str, resolved: dict[str, object]) -> str | None:
            if not self._provenance.tainted:
                return None
            # A request that would be refused anyway is refused, not read back.
            ToolFields.validate(name, resolved, self._projects)
            # The one exemption: the fixed denial text, with no model words, of
            # the permission prompt the worker is showing now.
            if resolved.get("choice") == "deny" and await self._readback.is_pending(
                resolved
            ):
                return None
            return self._gate.check(key, await self._readback.of(name, resolved))

        return hold
