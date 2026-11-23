"""Retain active managed sessions independently of ordinary hook context."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.hook_session_context import SessionContext


@final
class HookSessions:
    """Pin managed contexts until their workers exit; bound all other contexts."""

    __slots__ = ("_ordinary", "_workers")
    _ordinary: OrderedDict[str, SessionContext]
    _workers: dict[str, SessionContext]
    LIMIT = 64

    def __new__(cls) -> Self:
        """Start with no live sessions in either retention group."""
        self = super().__new__(cls)
        self._ordinary = OrderedDict()
        self._workers = {}
        return self

    def get(self, session: str) -> SessionContext | None:
        """Look up a managed or ordinary session without refreshing its age."""
        return self._workers.get(session) or self._ordinary.get(session)

    def remember(self, session: str, context: SessionContext, *, managed: bool) -> None:
        """Retain a context according to its lifetime."""
        if managed:
            self._ordinary.pop(session, None)
            self._workers[session] = context
        elif session not in self._workers:
            self._ordinary[session] = context
            self._ordinary.move_to_end(session)
            if len(self._ordinary) > self.LIMIT:
                self._ordinary.popitem(last=False)

    def release(self, session: str) -> None:
        """Allow the context of a terminated worker to age out."""
        if context := self._workers.pop(session, None):
            self.remember(session, context, managed=False)
