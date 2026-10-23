"""The reason an HTTP API's JSON error body gives, read without echoing raw text."""

from __future__ import annotations

import re
from typing import Final, Self, cast, final

from punt_vox.log_sanitize import SANITIZER

__all__ = ["ErrorBody"]

_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9_.-]{1,64}")


@final
class ErrorBody:
    """Read the server's reason out of a decoded error body.

    The reason lives under ``detail`` in one of three shapes: a string, a
    mapping carrying ``message`` (or, failing that, a ``status`` code), or a
    list of validation errors each carrying ``msg``. Anything else -- above all
    a body that never decoded and arrived as raw text, which can be an HTML
    error page or a proxy's banner -- gives no reason, so none is echoed.
    """

    __slots__ = ("_detail",)
    # object: the decoded JSON is untyped until a shape below matches it.
    _detail: object

    def __new__(cls, body: object) -> Self:
        self = super().__new__(cls)
        match body:
            case {"detail": object() as detail}:
                self._detail = detail
            case _:
                self._detail = None
        return self

    @property
    def message(self) -> str | None:  # None: the body gives no reason
        """Return the server's human-readable reason for the failure, on one line.

        The reason is the server's text, and every error built from it renders
        it verbatim in the daemon log; its control characters are escaped here,
        where the text enters vox, so no consumer can write a raw newline.
        """
        reason = self._reason
        return None if reason is None else SANITIZER.escape(reason)

    @property
    def _reason(self) -> str | None:  # None: the body gives no reason
        """Return the server's reason as it sent it."""
        match self._detail:
            case str() as text if text:
                return text
            case {"message": str() as text} if text:
                return text
            case {"status": str() as text} if text:
                return text
            case list():
                return self._first_msg()
            case _:
                return None

    @property
    def status(self) -> str | None:  # None: no status, or one unsafe to log
        """Return the server's machine-readable code under ``detail.status``.

        Only an identifier -- letters, digits, ``_``, ``.``, ``-``, at most 64
        of them -- is returned: the code is written to the log, and anything
        else could carry a newline into it or echo a credential.
        """
        match self._detail:
            case {"status": str() as code} if _IDENTIFIER.fullmatch(code):
                return code
            case _:
                return None

    def _first_msg(self) -> str | None:  # None: the first error carries no msg
        """Return the ``msg`` of the first validation error in the detail list."""
        match cast("list[object]", self._detail)[:1]:
            case [{"msg": str() as text}] if text:
                return text
            case _:
                return None
