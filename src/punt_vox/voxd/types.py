"""Type definitions for the voxd package."""

from __future__ import annotations

from typing import Protocol

from starlette.websockets import WebSocket  # noqa: TC002

__all__ = ["MessageHandler"]


class MessageHandler(Protocol):
    """Protocol for WebSocket message handlers."""

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None: ...
