"""Find the resources on the account whose text carries a marker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self, cast, final

from punt_vox.voxd.voice_agent.fault import ConvAIError, SweepIncompleteError
from punt_vox.voxd.voice_agent.reply import Reply

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.voice_agent.wire import Wire

__all__ = ["Listing"]

_PAGE_SIZE: Final = 100  # The most a listing returns per page.
_MAX_PAGES: Final = 100  # A bound, so a misbehaving cursor cannot spin the walk.


@dataclass(frozen=True, slots=True)
class _Page:
    """One page of a listing: the marked entries' ids, and where the next is."""

    ids: tuple[str, ...]
    next_cursor: str | None  # None on the last page.


@dataclass(frozen=True, slots=True)
class _Kind:
    """Where one kind of resource is listed, and where its id and text sit."""

    path: str
    items: str  # The reply field holding the page's entries.
    id_field: str
    text_field: tuple[str, ...]


_AGENTS: Final = _Kind("/agents", "agents", "agent_id", ("name",))
_TOOLS: Final = _Kind("/tools", "tools", "id", ("tool_config", "description"))


@final
class Listing:
    """Walk a paged ElevenLabs listing from its first page to its last."""

    __slots__ = ("_wire",)
    _wire: Wire

    def __new__(cls, wire: Wire) -> Self:
        """Bind the connection the listings are read over."""
        self = super().__new__(cls)
        self._wire = wire
        return self

    def marked_agents(
        self, marker: str, expired: Callable[[], bool]
    ) -> tuple[str, ...]:
        """Return the ids of every agent on the account whose name has *marker*.

        Raises :class:`SweepIncompleteError`, carrying what it found, once
        *expired* answers True between pages.
        """
        return self._marked(_AGENTS, marker, expired)

    def marked_tools(self, marker: str, expired: Callable[[], bool]) -> tuple[str, ...]:
        """Return the ids of every tool whose description has *marker*."""
        return self._marked(_TOOLS, marker, expired)

    def _marked(
        self, kind: _Kind, marker: str, expired: Callable[[], bool]
    ) -> tuple[str, ...]:
        """Return the ids at *kind* whose text contains *marker*.

        Raises :class:`ConvAIError` for a reply that is not a listing, a cursor
        that repeats, or a listing that outlasts the page bound.
        """
        found: list[str] = []
        seen: set[str] = set()
        cursor: str | None = None
        for page_number in range(_MAX_PAGES):
            # The first page is always read, so an earlier listing that spent
            # the budget cannot leave this one unread.
            if page_number and expired():
                msg = f"ElevenLabs GET {kind.path} ran out of time"
                raise SweepIncompleteError(msg, tuple(found))
            reply = self._wire.request("GET", kind.path, params=self._params(cursor))
            page = self._page(reply, kind, marker)
            found.extend(page.ids)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(found)
            if cursor in seen:
                msg = f"ElevenLabs GET {kind.path} repeated a cursor"
                raise ConvAIError(msg)
            seen.add(cursor)
        msg = f"ElevenLabs GET {kind.path} did not end after {_MAX_PAGES} pages"
        raise ConvAIError(msg)

    @staticmethod
    def _params(cursor: str | None) -> dict[str, str]:
        params = {"page_size": str(_PAGE_SIZE)}
        if cursor is not None:
            params["cursor"] = cursor
        return params

    def _page(self, reply: Reply, kind: _Kind, marker: str) -> _Page:
        """Read one page and keep the ids of the entries that carry *marker*.

        An entry with no text cannot carry it and is skipped. Anything else
        unreadable is an error: a skipped entry might be the resource to delete.
        """
        raw = reply.value(kind.items)
        if not isinstance(raw, list):
            msg = f"ElevenLabs reply is missing {kind.items}"
            raise ConvAIError(msg)
        ids: list[str] = []
        for entry in cast("list[object]", raw):
            if not isinstance(entry, dict):
                msg = f"ElevenLabs {kind.items} has an entry that is not an object"
                raise ConvAIError(msg)
            fields = cast("dict[str, object]", entry)
            text = self._text(fields, kind.text_field)
            if text is None or marker not in text:
                continue
            resource_id = fields.get(kind.id_field)
            if not isinstance(resource_id, str):
                msg = f"ElevenLabs {kind.items} has a marked entry with no usable id"
                raise ConvAIError(msg)
            ids.append(Reply.resource_id(resource_id))
        return _Page(tuple(ids), self._cursor(reply, kind))

    @staticmethod
    def _cursor(reply: Reply, kind: _Kind) -> str | None:
        """Return where the next page is; None when this was the last."""
        has_more = reply.value("has_more")
        if not isinstance(has_more, bool):
            msg = f"ElevenLabs {kind.items} listing has no boolean has_more"
            raise ConvAIError(msg)
        if not has_more:
            return None
        cursor = reply.value("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            msg = "ElevenLabs listing has more pages but no cursor"
            raise ConvAIError(msg)
        return cursor

    @staticmethod
    def _text(entry: object, path: tuple[str, ...]) -> str | None:
        """Return the text at *path*; None only if it is absent, null or empty."""
        value: object = entry
        for name in path:
            if value is None:
                return None
            if not isinstance(value, dict):
                msg = (
                    f"ElevenLabs listing has a parent of {name!r} that is not an object"
                )
                raise ConvAIError(msg)
            value = cast("dict[str, object]", value).get(name)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            msg = f"ElevenLabs listing has {path[-1]!r} that is not text"
            raise ConvAIError(msg)
        return value
