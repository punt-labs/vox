"""A parsed ``settings.json`` object, narrowed only at ``permissions.allow``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Self, cast, final

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["SettingsDocument"]


@final
class SettingsDocument:
    """The user's whole settings object, read and rewritten at one key path.

    ``dict[str, Any]`` is the JSON wire boundary -- the shape is the user's whole
    settings file, narrowed only at ``permissions.allow``, the one place this
    class writes. A *present but malformed* value is a boundary error
    (PY-EH-1/PY-EH-8), never an empty default: resetting it would silently
    discard the user's settings. *origin* names the file in every error.
    """

    __slots__ = ("_data", "_origin")

    _data: dict[str, Any]
    _origin: Path

    def __new__(cls, data: dict[str, Any], origin: Path) -> Self:
        self = super().__new__(cls)
        self._data = data
        self._origin = origin
        return self

    @classmethod
    def parse(cls, text: str, origin: Path) -> Self:
        """Return the document *text* holds; empty or blank text is ``{}``.

        Raise ``ValueError`` when the root is not a JSON object.
        """
        if not text.strip():
            return cls({}, origin)
        # The wire boundary: JSON deserialization yields object until narrowed.
        parsed: object = json.loads(text)
        if not isinstance(parsed, dict):
            msg = f"{origin}: settings root must be a JSON object"
            raise ValueError(msg)
        return cls(cast("dict[str, Any]", parsed), origin)

    @property
    def allow(self) -> list[str]:
        """Return the current ``permissions.allow`` entries as strings, else ``[]``.

        Presence is keyed on membership, never on truthiness -- a present but
        ``null`` ``permissions`` (or ``permissions.allow``) is malformed and
        raises, exactly like a string or number would, rather than being read as
        absent and silently overwritten. Only a genuinely *absent* key is safe to
        treat as empty and create later.
        """
        if "permissions" not in self._data:
            return []
        perms = self._permissions()
        if "allow" not in perms:
            return []
        allow: Any = perms["allow"]
        if not isinstance(allow, list):
            msg = f"{self._origin}: permissions.allow must be a JSON array"
            raise ValueError(msg)
        return [str(entry) for entry in cast("list[object]", allow)]

    def set_allow(self, allow: list[str]) -> None:
        """Set ``permissions.allow`` to *allow*, creating an absent ``permissions``.

        A present ``permissions`` must be an object (``ValueError`` otherwise),
        so this never overwrites the user's data.
        """
        if "permissions" not in self._data:
            self._data["permissions"] = {}
        self._permissions()["allow"] = allow

    def dumps(self) -> str:
        """Return the document serialized as the file's new text."""
        return json.dumps(self._data, indent=2) + "\n"

    def _permissions(self) -> dict[str, Any]:
        """Return the present ``permissions`` object, raising if it is not one."""
        perms: Any = self._data["permissions"]
        if not isinstance(perms, dict):
            msg = f"{self._origin}: permissions must be a JSON object"
            raise ValueError(msg)
        return cast("dict[str, Any]", perms)
