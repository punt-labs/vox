"""The reason an HTTP API's JSON error body gives, read without echoing raw text."""

from __future__ import annotations

from typing import Self, cast, final

__all__ = ["ErrorBody"]


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
        """Return the server's human-readable reason for the failure."""
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

    def _first_msg(self) -> str | None:  # None: the first error carries no msg
        """Return the ``msg`` of the first validation error in the detail list."""
        match cast("list[object]", self._detail)[:1]:
            case [{"msg": str() as text}] if text:
                return text
            case _:
                return None
