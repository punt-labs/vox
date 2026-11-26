"""Web search through Exa, with the key read from the daemon's environment."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final, Self, cast, final

import httpx

from punt_vox.voxd.voice_call.lookup.snippet import Snippet

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ExaSearch"]

_URL: Final = "https://api.exa.ai/search"
_TIMEOUT_S: Final = 15.0
RESULTS: Final = 3
_TEXT_CHARS: Final = 600
KEY_VARIABLE: Final = "EXA_API_KEY"


@final
class ExaSearch:
    """Run one fixed-shape query against Exa and return short snippets."""

    __slots__ = ("_client", "_key")
    _key: str
    _client: httpx.AsyncClient

    def __new__(cls, api_key: str, *, client: httpx.AsyncClient | None = None) -> Self:
        """Bind the key, which leaves this object only in the request header."""
        if not api_key.strip():
            msg = f"{KEY_VARIABLE} is empty"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._key = api_key
        self._client = client if client is not None else httpx.AsyncClient()
        return self

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Self | None:
        """Return a search bound to the key in the environment, or None without one."""
        key = (os.environ if environ is None else environ).get(KEY_VARIABLE, "")
        return cls(key) if key.strip() else None

    async def search(self, query: str) -> list[Snippet]:
        """Return up to three snippets for ``query``; the key is never in a message."""
        body = {
            "query": query,
            "numResults": RESULTS,
            "contents": {"text": {"maxCharacters": _TEXT_CHARS}},
        }
        try:
            response = await self._client.post(
                _URL,
                json=body,
                headers={"x-api-key": self._key},
                timeout=_TIMEOUT_S,
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.HTTPError as exc:
            msg = f"web search failed ({type(exc).__name__})"
            raise ValueError(msg) from None
        if response.is_error:
            msg = f"web search failed (HTTP {response.status_code})"
            raise ValueError(msg)
        return self._snippets(response)

    @staticmethod
    def _snippets(response: httpx.Response) -> list[Snippet]:
        try:
            data: object = response.json()
        except ValueError:
            msg = "web search failed (unreadable reply)"
            raise ValueError(msg) from None
        rows = (
            cast("dict[str, object]", data).get("results")
            if isinstance(data, dict)
            else None
        )
        if not isinstance(rows, list):
            return []
        found: list[Snippet] = []
        for row in cast("list[object]", rows):
            if isinstance(row, dict):
                fields = cast("dict[str, object]", row)
                found.append(
                    Snippet(
                        str(fields.get("title") or ""), str(fields.get("text") or "")
                    )
                )
        return found
