"""The error a provider request raises when the server answered with a failure."""

from __future__ import annotations

from typing import Self

__all__ = ["ProviderStatusError"]


class ProviderStatusError(Exception):
    """The provider answered with an error status no vox type names more exactly.

    A rejected key has its own type (``ProviderAuthError``); this is every other
    answered failure -- a rate limit, a 5xx, a prompt the server refused -- in
    vox's terms, so a caller can route on the status without importing the
    provider's SDK. Not a ``ValueError``: the wire keeps it on the opaque fault
    path, as it did the SDK's own error, rather than sending server text to the
    caller as a rejection.
    """

    _provider_name: str
    # None: the SDK reported no status for the answer.
    _status_code: int | None
    # None: the response body carried no human-readable message.
    _detail: str | None

    # __new__ is the constructor (PY-CC-1); BaseException's permissive __init__
    # takes the same arguments, which pyright cannot see.
    def __new__(  # pyright: ignore[reportInconsistentConstructor]
        cls, provider: str, status_code: int | None, detail: str | None
    ) -> Self:
        self = super().__new__(cls, provider, status_code, detail)
        self._provider_name = provider
        self._status_code = status_code
        self._detail = detail
        return self

    def __str__(self) -> str:
        """Render the status and the server's message, whichever there are."""
        answered = "with " if self._status_code is None else ""
        message = f"provider {self._provider_name!r} answered {answered}{self._status}"
        return message if self._detail is None else f"{message}: {self._detail}"

    @property
    def provider_name(self) -> str:
        """Return the provider that answered."""
        return self._provider_name

    @property
    def status_code(self) -> int | None:  # None: the SDK reported no status
        """Return the HTTP status the provider answered with."""
        return self._status_code

    @property
    def detail(self) -> str | None:  # None: the body carried no message
        """Return the server's human-readable message."""
        return self._detail

    @property
    def reason(self) -> str:
        """Return the server's message, or the status when it gave none."""
        return self._detail if self._detail is not None else self._status

    @property
    def _status(self) -> str:
        """Return the status as a phrase: ``HTTP 500``, or ``no HTTP status``."""
        return (
            "no HTTP status"
            if self._status_code is None
            else f"HTTP {self._status_code}"
        )
