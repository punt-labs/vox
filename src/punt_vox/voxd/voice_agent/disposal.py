"""Delete remote agents and tools only when they carry this host's marker."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_agent.fault import ConvAIError, ForeignResourceError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.voice_agent.control import ConvAIControl
    from punt_vox.voxd.voice_agent.host_marker import HostMarker

__all__ = ["Disposal", "Undeleted"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Undeleted:
    """A resource a batch delete left in place, and why."""

    resource_id: str
    reason: str  # Names the kind and the id, so it reads alone in an error.

    @staticmethod
    def ids(left: tuple[Undeleted, ...]) -> tuple[str, ...]:
        """Return the ids of *left*."""
        return tuple(item.resource_id for item in left)


@final
class Disposal:
    """The one place voxd deletes remote resources.

    A resource without the marker is never deleted. That is a failure like any
    other: the id stays recorded and is reported, never silently dropped.
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
        """Delete one agent; raise :class:`ConvAIError` if it was not deleted."""
        self._delete("agent", agent_id, self._control.delete_agent)

    def tool(self, tool_id: str) -> None:
        """Delete one tool; raise :class:`ConvAIError` if it was not deleted."""
        self._delete("tool", tool_id, self._control.delete_tool)

    def agents(self, ids: tuple[str, ...]) -> tuple[Undeleted, ...]:
        """Delete each agent; return the ones left in place. Never raises."""
        return self._each("agent", ids, self._control.delete_agent)

    def tools(self, ids: tuple[str, ...]) -> tuple[Undeleted, ...]:
        """Delete each tool; return the ones left in place. Never raises."""
        return self._each("tool", ids, self._control.delete_tool)

    def _each(
        self, kind: str, ids: tuple[str, ...], delete: Callable[[str, str], None]
    ) -> tuple[Undeleted, ...]:
        left: list[Undeleted] = []
        for resource_id in ids:
            try:
                self._delete(kind, resource_id, delete)
            except ConvAIError as exc:
                logger.warning("Could not delete old %s: %s", kind, exc)
                left.append(Undeleted(resource_id, str(exc)))
        return tuple(left)

    def _delete(
        self, kind: str, resource_id: str, delete: Callable[[str, str], None]
    ) -> None:
        try:
            delete(resource_id, self._marker.text())
        except ForeignResourceError as exc:
            msg = f"{kind} {resource_id}: lacks this host's marker; not deleted"
            raise ConvAIError(msg) from exc
        except ConvAIError as exc:
            raise ConvAIError(f"{kind} {resource_id}: {exc}") from exc
