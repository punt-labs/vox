"""Escape untrusted text so one value can never forge or corrupt a log line."""

from __future__ import annotations

import logging
from typing import Self, final

__all__ = ["SANITIZER", "LogSanitizer", "SanitizingFormatter"]


@final
class LogSanitizer:
    r"""Map every C0/C1 control char and Unicode line separator to a visible escape.

    Untrusted text -- a wire field, a subprocess's stderr, a provider error body
    -- can forge a second log record via an embedded newline or corrupt a
    terminal via a raw control byte on ``cat``. :meth:`escape` translates every
    such code point (all of C0, DEL, and all of C1 -- C1 includes CSI U+009B,
    which starts an ANSI escape sequence) to a visible ``\xXX`` / ``\uXXXX`` (or
    ``\n`` / ``\t``) escape, so smuggled bytes stay auditable and stay one line.
    """

    __slots__ = ("_table",)
    _table: dict[int, str]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._table = cls._build_table()
        return self

    def escape(self, value: str) -> str:
        """Return *value* with every control / line-breaking code point escaped."""
        return value.translate(self._table)

    @staticmethod
    def _build_table() -> dict[int, str]:
        # \t \n \r keep their conventional short escapes; every other C0 control
        # byte and DEL becomes \xXX. NEL (U+0085), LINE SEPARATOR (U+2028) and
        # PARAGRAPH SEPARATOR (U+2029) -- which str.splitlines also treats as
        # breaks -- become \uXXXX so a Unicode-aware viewer cannot render a
        # smuggled one of them as a second visual record.
        short = {ord("\t"): "\\t", ord("\n"): "\\n", ord("\r"): "\\r"}
        controls = (*range(0x20), *range(0x7F, 0xA0))  # C0, DEL, and all of C1
        table = {cp: short.get(cp, f"\\x{cp:02x}") for cp in controls}
        table.update({cp: f"\\u{cp:04x}" for cp in (0x85, 0x2028, 0x2029)})
        return table


SANITIZER = LogSanitizer()


@final
class SanitizingFormatter(logging.Formatter):
    """Escape the whole formatted line so no single field can forge a second line.

    The last line of defense at every daemon file write. The format string renders
    ``name``/``level``/``message`` -- any of which may carry a client-shipped or
    provider-error value -- and this then escapes the *final* string, so a newline
    or C1/CSI byte in any field becomes a visible escape and the record stays
    exactly one physical line. Formatting the message first, escaping second, is
    what makes it field-agnostic: it does not matter which field smuggled the byte.
    """

    __slots__ = ()

    def format(self, record: logging.LogRecord) -> str:
        """Return the formatted record with every control byte escaped."""
        return SANITIZER.escape(super().format(record))
