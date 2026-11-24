"""Typed access to a parsed JSON object at the deserialization boundary.

``JsonObject`` wraps the ``dict[str, object]`` that ``json.loads`` yields and
turns every field read into a typed accessor that *raises* on a missing or
wrong-typed field (PY-EH-8), never returning ``None`` for "couldn't produce a
value". This is the one place the Programs domain meets untyped data; keeping
the coercion on a value object (not scattered free functions) is the WireContext
pattern (PY-OO-7).
"""

from __future__ import annotations

import json
from typing import Self, cast, final

__all__ = ["JsonObject"]


@final
class JsonObject:
    """A parsed JSON object with typed, raising field access."""

    __slots__ = ("_data", "_where")
    _data: dict[str, object]
    _where: str

    def __new__(cls, data: dict[str, object], where: str) -> Self:
        """Create a json object."""
        self = super().__new__(cls)
        self._data = data
        self._where = where
        return self

    @classmethod
    def parse(cls, text: str, where: str) -> Self:
        """Parse ``text`` as a JSON object, raising if it is not one.

        Raises:
            ValueError: If the text is not a JSON object.
        """
        parsed: object = json.loads(text)
        return cls.coerce(parsed, where)

    @classmethod
    def coerce(cls, value: object, where: str) -> Self:
        """Wrap an already-parsed value, raising if it is not an object.

        Raises:
            ValueError: If value is not a dictionary.
        """
        if not isinstance(value, dict):
            msg = f"{where} must be a JSON object"
            raise ValueError(msg)
        return cls(cast("dict[str, object]", value), where)

    def require_str(self, field: str) -> str:
        """Return a required string field, raising if absent or wrong-typed.

        Raises:
            ValueError: If a required field is missing or a present field has the wrong
                type.
        """
        value = self._require(field)
        if not isinstance(value, str):
            raise self._wrong(field, "a string")
        return value

    def require_int(self, field: str) -> int:
        """Return a required integer field (``bool`` is rejected).

        Raises:
            ValueError: If a required field is missing or a present field has the wrong
                type.
        """
        value = self._require(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._wrong(field, "an integer")
        return value

    def require_bool(self, field: str) -> bool:
        """Return a required boolean field, raising if absent or wrong-typed.

        Raises:
            ValueError: If a required field is missing or a present field has the wrong
                type.
        """
        value = self._require(field)
        if not isinstance(value, bool):
            raise self._wrong(field, "a boolean")
        return value

    def require_object(self, field: str) -> JsonObject:
        """Return a required nested object as a :class:`JsonObject`.

        Raises:
            ValueError: If a required field is missing or a present field has the wrong
                type.
        """
        return JsonObject.coerce(self._require(field), f"{self._where}.{field}")

    def require_list(self, field: str) -> tuple[object, ...]:
        """Return a required array field as a tuple of raw elements.

        Raises:
            ValueError: If a required field is missing or a present field has the wrong
                type.
        """
        value = self._require(field)
        if not isinstance(value, list):
            raise self._wrong(field, "a list")
        return tuple(cast("list[object]", value))

    def opt_list(self, field: str) -> tuple[object, ...]:
        """Return an array field as a tuple, or an empty tuple when absent or null.

        Raises:
            ValueError: If a present, non-null field has the wrong type.
        """
        if self._data.get(field) is None:
            return ()
        return self.require_list(field)

    def opt_object(self, field: str) -> JsonObject | None:
        """Return a nested object field, or ``None`` when the key is absent or null.

        ``None`` is the documented "field not present" contract (a nullable wire
        object such as ``now_playing``), not a parse failure -- a present,
        non-null, non-object value still raises.

        Raises:
            ValueError: If a present, non-null field has the wrong type.
        """
        value = self._data.get(field)
        if value is None:
            return None
        return JsonObject.coerce(value, f"{self._where}.{field}")

    def opt_int(self, field: str) -> int | None:
        """Return an integer field, or ``None`` when the key is absent or null.

        ``None`` here is the documented "field not present" contract, not a
        parse failure -- a present-but-wrong-typed value still raises. A JSON
        ``null`` is treated as absence, matching :meth:`opt_object`.

        Raises:
            ValueError: If a present, non-null field has the wrong type.
        """
        if self._data.get(field) is None:
            return None
        return self.require_int(field)

    def opt_str(self, field: str) -> str | None:
        """Return a string field, or ``None`` when the key is absent or null.

        Raises:
            ValueError: If a present, non-null field has the wrong type.
        """
        if self._data.get(field) is None:
            return None
        return self.require_str(field)

    def opt_bool(self, field: str) -> bool | None:
        """Return a boolean field, or ``None`` when absent or null; else raises.

        Raises:
            ValueError: If a present, non-null field has the wrong type.
        """
        if self._data.get(field) is None:
            return None
        return self.require_bool(field)

    def _require(self, field: str) -> object:
        if field not in self._data:
            msg = f"{self._where} is missing required field {field!r}"
            raise ValueError(msg)
        return self._data[field]

    def _wrong(self, field: str, expected: str) -> ValueError:
        return ValueError(f"{self._where} field {field!r} must be {expected}")
