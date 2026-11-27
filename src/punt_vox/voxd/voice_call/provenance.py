"""Track whether untrusted text has entered the call's conversation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self, final

if TYPE_CHECKING:
    from collections.abc import Collection

__all__ = ["CallProvenance"]

# Provider system tools that carry no outside text.
_QUIET_SYSTEM_TOOLS: Final = frozenset({"end_call", "skip_turn"})


@final
class CallProvenance:
    """Record untrusted text for the rest of the call and count each arrival.

    The call is tainted from the first untrusted text on; nothing untaints it.
    The epoch counts arrivals so a clearance granted before newer untrusted
    text can be told apart from one granted after it.
    """

    __slots__ = ("_epoch",)
    _epoch: int

    def __new__(cls) -> Self:
        """Start a call with no untrusted text."""
        self = super().__new__(cls)
        self._epoch = 0
        return self

    @property
    def tainted(self) -> bool:
        """Return whether untrusted text has reached the conversation."""
        return self._epoch > 0

    @property
    def epoch(self) -> int:
        """Return how many times untrusted text has arrived."""
        return self._epoch

    def taint(self) -> None:
        """Record that untrusted text reached the conversation."""
        self._epoch += 1

    def observe_tool_response(
        self, name: str, tool_type: str | None, voxd_tools: Collection[str]
    ) -> None:
        """Taint on any tool response that is not a voxd client tool.

        A missing type or an unknown name taints: only the two quiet system
        tools and voxd's own typed client tools are known to carry nothing.
        ``tool_type`` is ``None`` when the provider omitted it.
        """
        if tool_type == "system" and name in _QUIET_SYSTEM_TOOLS:
            return
        if tool_type == "client" and name in voxd_tools:
            return
        self.taint()
