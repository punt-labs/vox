"""Exactly-once admission and response claims for conversation client tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.events import ClientToolCall


@final
class ToolCalls:
    """Remember tool IDs independently of their outgoing wire messages."""

    __slots__ = ("_calls",)
    _calls: dict[str, bool]

    def __new__(cls) -> Self:
        """Create an empty conversation's tool-call ledger."""
        self = super().__new__(cls)
        self._calls = {}
        return self

    def admit(self, call: ClientToolCall) -> bool:
        """Remember new calls and reject duplicates, even after a reply."""
        if call.tool_call_id in self._calls:
            return False
        self._calls[call.tool_call_id] = call.expects_response
        return True

    def claim(self, tool_call_id: str) -> None:
        """Consume the one response allowed for a pending client tool call."""
        if not self._calls.get(tool_call_id):
            msg = "No pending ElevenLabs client tool call expects that result"
            raise ValueError(msg)
        self._calls[tool_call_id] = False
