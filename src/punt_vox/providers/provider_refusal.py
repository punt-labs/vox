"""A provider's refusal of its credentials, with the server's reason for it."""

from __future__ import annotations

import re
from typing import Final, Self

from punt_vox.types_provider_errors import ProviderAuthError

__all__ = ["ProviderRefusalError"]

# A server error code quoted on the wire must be a plain identifier: the server
# chose the text, and an unchecked one could carry a newline into a log line.
_CODE: Final = re.compile(r"[A-Za-z0-9_.\-]{1,64}")


class ProviderRefusalError(ProviderAuthError):
    """The provider refused the credentials (HTTP 401 or 403), and maybe said why.

    A :class:`ProviderAuthError`, so it is routed and treated the same -- sent
    verbatim, permanent for music and calls -- but its message carries the
    server's error code when there is one, because a refusal is not always a bad
    key: a project without access to the model, an unsupported region, or an
    exhausted quota each want a different fix.
    """

    # None: the server gave no usable code, and the message is the parent's.
    _reason: str | None

    # __new__ is the constructor (PY-CC-1); BaseException's permissive __init__
    # takes the same arguments, which pyright cannot see -- as on the parent.
    def __new__(cls, provider: str, status_code: int, reason: str | None) -> Self:  # pyright: ignore[reportInconsistentConstructor]
        self = super().__new__(cls, provider, status_code)
        self._reason = reason
        return self

    @classmethod
    def from_code(cls, provider: str, status_code: int, code: str | None) -> Self:
        """Return the refusal for the server's error *code*, kept only when it is
        a plain identifier; *code* is ``None`` when the body carried none."""
        usable = code is not None and _CODE.fullmatch(code) is not None
        return cls(provider, status_code, code if usable else None)

    def __str__(self) -> str:
        """Render the refusal with the server's reason, when it gave one."""
        if self._reason is None:
            return super().__str__()
        return (
            f"provider {self.provider_name!r} refused the request "
            f"({self.answer}); run `vox doctor`"
        )

    @property
    def reason(self) -> str | None:  # None: the server gave no usable code
        """Return the server's error code."""
        return self._reason

    @property
    def answer(self) -> str:
        """Return the answer as a phrase: ``HTTP 403: quota_exceeded``, or
        ``HTTP 401`` when the server gave no usable code."""
        status = f"HTTP {self.status_code}"
        return status if self._reason is None else f"{status}: {self._reason}"
