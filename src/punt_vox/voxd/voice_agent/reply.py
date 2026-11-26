"""Read the fields of one ElevenLabs JSON reply."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, Self, cast, final

from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    import httpx

__all__ = ["Reply"]

_RESOURCE_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")


@final
class Reply:
    """A successful reply whose JSON body is read, never trusted, field by field."""

    __slots__ = ("_response",)
    _response: httpx.Response

    def __new__(cls, response: httpx.Response) -> Self:
        """Wrap *response*."""
        self = super().__new__(cls)
        self._response = response
        return self

    @staticmethod
    def resource_id(value: str) -> str:
        """Validate a remote resource ID before using it in a request path."""
        if not _RESOURCE_ID.fullmatch(value):
            msg = "Invalid ElevenLabs resource ID"
            raise ConvAIError(msg)
        return value

    def is_absent(self) -> bool:
        """Return whether the resource the request named does not exist."""
        return self._response.status_code == 404

    def string(self, field: str) -> str:
        """Return the nonempty string at *field*, else raise :class:`ConvAIError`."""
        value = self._at((field,))
        if isinstance(value, str) and value:
            return value
        msg = f"ElevenLabs reply is missing {field}"
        raise ConvAIError(msg)

    def created_id(self, field: str, unknown_note: str) -> str:
        """Return the created resource's id; on failure say it may be unrecorded."""
        try:
            return self.resource_id(self.string(field))
        except ConvAIError as exc:
            raise ConvAIError(f"{exc}{unknown_note}") from None

    def text_at(self, *path: str) -> str | None:
        """Return the string reached by *path*, or None if the shape differs."""
        value = self._at(path, strict=False)
        return value if isinstance(value, str) else None

    def _at(self, path: tuple[str, ...], *, strict: bool = True) -> object:
        value = self._json(strict=strict)
        for name in path:
            if not isinstance(value, dict):
                return None
            value = cast("dict[str, object]", value).get(name)
        return value

    def _json(self, *, strict: bool = True) -> object:
        try:
            return self._response.json()
        except (ValueError, RecursionError):
            if strict:
                msg = "ElevenLabs returned invalid JSON"
                raise ConvAIError(msg) from None
            return None
