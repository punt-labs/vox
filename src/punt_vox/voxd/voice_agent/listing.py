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
    """One page of a listing: ``(id, text)`` entries, and where the next is."""

    entries: tuple[tuple[str, str], ...]
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
            )
            found.extend(i for i, text in page.entries if marker in text)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(found)
            if cursor in seen:
                break
            seen.add(cursor)
        msg = f"ElevenLabs GET {path} did not end after {len(seen) + 1} pages"
        raise ConvAIError(msg)

    def _page(
        self,
        reply: Reply,
        items: str,
        id_field: str,
        text_field: tuple[str, ...],
    ) -> _Page:
        """Read one page; an entry with no text has no marker and is left out."""
        raw = reply.value(items)
        if not isinstance(raw, list):
            msg = f"ElevenLabs reply is missing {items}"
            raise ConvAIError(msg)
        entries: list[tuple[str, str]] = []
        for entry in cast("list[object]", raw):
            if not isinstance(entry, dict):
                continue
            fields = cast("dict[str, object]", entry)
            text = self._text(fields, text_field)
            resource_id = fields.get(id_field)
            if text is not None and isinstance(resource_id, str):
                entries.append((Reply.resource_id(resource_id), text))
        if reply.value("has_more") is not True:
            return _Page(tuple(entries), None)
        cursor = reply.value("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            msg = "ElevenLabs listing has more pages but no cursor"
            raise ConvAIError(msg)
        return _Page(tuple(entries), cursor)

    @staticmethod
    def _text(entry: object, path: tuple[str, ...]) -> str | None:
        value: object = entry
        for name in path:
            if not isinstance(value, dict):
                return None
            value = cast("dict[str, object]", value).get(name)
        return value if isinstance(value, str) else None
