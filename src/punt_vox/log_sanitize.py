"""Escape untrusted text so one value can never forge or corrupt a log line."""

from __future__ import annotations

from typing import Self, final

__all__ = ["SANITIZER", "LogSanitizer"]


@final
class LogSanitizer:
    r"""Map C0/C1 control chars and Unicode line separators to visible escapes.

    Untrusted text -- a wire field, a subprocess's stderr, a provider error body
    -- can forge a second log record via an embedded newline or corrupt a
    terminal via a raw control byte on ``cat``. :meth:`escape` translates every
    such code point to a visible ``\xXX`` / ``\uXXXX`` (or ``\n`` / ``\t``)
    escape, so the smuggled bytes stay auditable and the record stays one line.
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
        table = {cp: short.get(cp, f"\\x{cp:02x}") for cp in (*range(0x20), 0x7F)}
        table.update({cp: f"\\u{cp:04x}" for cp in (0x85, 0x2028, 0x2029)})
        return table


SANITIZER = LogSanitizer()
