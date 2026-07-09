"""The shared lowercase-hex token base for :class:`AlbumId` and the fingerprint.

``AlbumId`` and ``PromptFingerprint`` are both short, immutable, validated
lowercase-hex strings that differ only in how they are *minted* -- an id from
``secrets.token_hex``, a fingerprint from a prompt-set hash. Everything else --
the hex validation, the ``value`` accessor, and the value-object dunders -- is
identical, so it lives here once. Each concrete token is a ``@final`` subclass
adding only its factory; the class is part of the token's identity, so two tokens
of different subclasses never compare equal even with the same hex value.
"""

from __future__ import annotations

from typing import ClassVar, Final, Self

__all__ = ["HexToken"]

_HEX_DIGITS: Final = frozenset("0123456789abcdef")


class HexToken:
    """A validated, immutable lowercase-hex identity -- subclass to name a token."""

    __slots__ = ("_value",)
    _value: str
    _LABEL: ClassVar[str] = "hex token"  # the noun used in validation errors

    def __new__(cls, value: str) -> Self:
        cleaned = value.strip()
        if not cleaned:
            msg = f"{cls._LABEL} must be non-empty"
            raise ValueError(msg)
        if not all(char in _HEX_DIGITS for char in cleaned):
            msg = f"{cls._LABEL} must be lowercase hex: {value!r}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._value = cleaned
        return self

    @property
    def value(self) -> str:
        """Return the hex string."""
        return self._value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HexToken) or type(other) is not type(self):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash((type(self), self._value))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._value!r})"

    def __str__(self) -> str:
        return self._value
