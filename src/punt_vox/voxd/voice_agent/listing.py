"""Find the resources on the account whose text carries a marker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self, cast, final

from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.reply import Reply

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.wire import Wire

__all__ = ["Listing"]

_PAGE_SIZE: Final = 100  # The most a listing returns per page.
_MAX_PAGES: Final = 100  # A bound, so a misbehaving cursor cannot spin the walk.


@dataclass(frozen=True, slots=True)
class _Page:
    """One page of a listing: the marked entries' ids, and where the next is."""

    ids: tuple[str, ...]
    next_cursor: str | None  # None on the last page.


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

    def marked_agents(self, marker: str) -> tuple[str, ...]:
        """Return the ids of every agent on the account whose name has *marker*."""
        return self._marked("/agents", "agents", "agent_id", ("name",), marker)

    def marked_tools(self, marker: str) -> tuple[str, ...]:
        """Return the ids of every tool whose description has *marker*."""
        return self._marked(
            "/tools", "tools", "id", ("tool_config", "description"), marker
        )

    def _marked(
        self,
        path: str,
        items: str,
        id_field: str,
        text_field: tuple[str, ...],
        marker: str,
    ) -> tuple[str, ...]:
        """Return the ids at *path* whose text at *text_field* contains *marker*.

        Raises :class:`ConvAIError` for a reply that is not a listing, a cursor
        that repeats, or a listing that outlasts the page bound.
        """
        found: list[str] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params = {"page_size": str(_PAGE_SIZE)}
            if cursor is not None:
                params["cursor"] = cursor
            page = self._page(
                self._wire.request("GET", path, params=params),
                items,
                id_field,
                text_field,
                marker,
            )
            found.extend(page.ids)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(found)
            if cursor in seen:
                msg = f"ElevenLabs GET {path} repeated a cursor"
                raise ConvAIError(msg)
            seen.add(cursor)
        msg = f"ElevenLabs GET {path} did not end after {_MAX_PAGES} pages"
        raise ConvAIError(msg)

    def _page(
        self,
        reply: Reply,
        items: str,
        id_field: str,
        text_field: tuple[str, ...],
        marker: str,
    ) -> _Page:
        """Read one page and keep the ids of the entries that carry *marker*.

        An entry with no text cannot carry it and is skipped. Anything else
        unreadable is an error: a skipped entry might be the resource to delete.
        """
        raw = reply.value(items)
        if not isinstance(raw, list):
            msg = f"ElevenLabs reply is missing {items}"
            raise ConvAIError(msg)
        ids: list[str] = []
        for entry in cast("list[object]", raw):
            if not isinstance(entry, dict):
                msg = f"ElevenLabs {items} listing has an entry that is not an object"
                raise ConvAIError(msg)
            fields = cast("dict[str, object]", entry)
            text = self._text(fields, text_field)
            if text is None or marker not in text:
                continue
            resource_id = fields.get(id_field)
            if not isinstance(resource_id, str):
                msg = f"ElevenLabs {items} listing has a marked entry with no usable id"
                raise ConvAIError(msg)
            ids.append(Reply.resource_id(resource_id))
        return _Page(tuple(ids), self._cursor(reply, items))

    @staticmethod
    def _cursor(reply: Reply, items: str) -> str | None:
        """Return where the next page is; None when this was the last."""
        has_more = reply.value("has_more")
        if not isinstance(has_more, bool):
            msg = f"ElevenLabs {items} listing has no boolean has_more"
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
        value: object = entry
        for name in path:
            if not isinstance(value, dict):
                return None
            value = cast("dict[str, object]", value).get(name)
        return value if isinstance(value, str) else None
