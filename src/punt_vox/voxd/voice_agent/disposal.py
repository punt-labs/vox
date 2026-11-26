"""Delete remote agents and tools only when they carry this host's marker."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent.fault import ConvAIError, ForeignResourceError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.voice_agent.control import ConvAIControl
    from punt_vox.voxd.voice_agent.host_marker import HostMarker

__all__ = ["Disposal"]

logger = logging.getLogger(__name__)


@final
class Disposal:
    """The one place voxd deletes remote resources.

    A resource without the marker is never deleted: a registration naming one
    is a tampered or copied file, so the id is dropped, not retried.
    """

    __slots__ = ("_control", "_marker")
    _control: ConvAIControl
    _marker: HostMarker

    def __new__(cls, control: ConvAIControl, marker: HostMarker) -> Self:
        """Bind the control that deletes and the marker a resource must carry."""
        self = super().__new__(cls)
        self._control, self._marker = control, marker
        return self

    def agent(self, agent_id: str) -> None:
        """Delete one agent; raise :class:`ConvAIError` if it could not be deleted."""
        self._delete("agent", agent_id, self._control.delete_agent)

    def tool(self, tool_id: str) -> None:
        """Delete one tool; raise :class:`ConvAIError` if it could not be deleted."""
        self._delete("tool", tool_id, self._control.delete_tool)

    def agents(self, ids: tuple[str, ...]) -> tuple[str, ...]:
        """Delete each agent; return the ids still to retry. Never raises."""
        return self._each("agent", ids, self._control.delete_agent)

    def tools(self, ids: tuple[str, ...]) -> tuple[str, ...]:
        """Delete each tool; return the ids still to retry. Never raises."""
        return self._each("tool", ids, self._control.delete_tool)

    def _each(
        self, kind: str, ids: tuple[str, ...], delete: Callable[[str, str], None]
    ) -> tuple[str, ...]:
        kept: list[str] = []
        for resource_id in ids:
            try:
                self._delete(kind, resource_id, delete)
            except ConvAIError as exc:
                logger.warning("Could not delete old %s %s: %s", kind, resource_id, exc)
                kept.append(resource_id)
        return tuple(kept)

    def _delete(
        self, kind: str, resource_id: str, delete: Callable[[str, str], None]
    ) -> None:
        try:
            delete(resource_id, self._marker.text())
        except ForeignResourceError:
            logger.warning(
                "Not deleting %s %s: it lacks this host's marker", kind, resource_id
            )
