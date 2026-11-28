"""Which tool a worker is still running, paired from its start and end hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from punt_vox.voxd.hook_context_projection import ToolUpdate

__all__ = ["ToolFlight"]


@final
class ToolFlight:
    """Pair each completion with the oldest unsettled start of the same call.

    Identical parallel calls share a tool and subject, so one completion must
    settle one start, not every start before it.
    """

    __slots__ = ("_tools",)
    _tools: Mapping[int, ToolUpdate]

    def __new__(cls, tools: Mapping[int, ToolUpdate]) -> Self:
        """Bind the tool updates by sender sequence."""
        self = super().__new__(cls)
        self._tools = tools
        return self

    def running_after(self, since: int) -> str | None:  # None when nothing runs.
        """Return the newest tool started after ``since`` with no completion."""
        waiting: dict[tuple[str, str], list[int]] = {}
        for sequence, update in sorted(self._tools.items()):
            if sequence <= since:
                continue
            starts = waiting.setdefault((update.tool, update.subject), [])
            if not update.completed:
                starts.append(sequence)
            elif starts:
                starts.pop(0)
        running = [sequence for starts in waiting.values() for sequence in starts]
        return self._tools[max(running)].tool if running else None
