"""The id a new recording is stored under: an explicit name or its text's default.

A take is stored under the client's explicit ``--name`` or, when there is none,
the content-addressed default for its text. :class:`RecordingName` is a value
object whose construction *is* the validation: an explicit name must be a
:class:`~punt_vox.bare_name.BareName` and must not be empty or whitespace-only,
since a blank id would list as an empty row no one can read or retype. It
governs naming a new take only; references to an existing recording are judged
by ``BareName`` alone, so a blank-named take recorded earlier stays reachable.
"""

from __future__ import annotations

from typing import Final, Self, final

from punt_vox.bare_name import BareName
from punt_vox.types import generate_filename

__all__ = ["RecordingName"]

_LABEL: Final = "recording name"


@final
class RecordingName:
    """A validated id for a new recording: an explicit name or the text's default."""

    __slots__ = ("_value",)

    _value: str

    def __new__(cls, name: str) -> Self:
        """Create a recording name from an explicit client-supplied *name*.

        Raises:
            ValueError: If the name is empty or whitespace-only, or is not a bare
                filename.
        """
        if not name.strip():
            raise ValueError(f"{_LABEL} must not be empty or whitespace")
        self = super().__new__(cls)
        self._value = BareName(name, _LABEL).value
        return self

    @classmethod
    def for_take(cls, name: str | None, text: str) -> Self:
        """Return *name*, or the content-addressed default for *text* when absent.

        Only ``None`` content-addresses; an explicit ``""`` is an invalid name.
        """
        return cls(generate_filename(text) if name is None else name)

    @property
    def value(self) -> str:
        """Return the validated id -- safe to join onto the recordings root."""
        return self._value
