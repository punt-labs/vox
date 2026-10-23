"""A caller's API key, set in the variable its provider reads, for one block."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.providers.credentials import ProviderCredentials

if TYPE_CHECKING:
    from collections.abc import Generator

__all__ = ["ApiKeyEnv"]

# provider -> the variable it reads its API key from. Derived from
# ProviderCredentials, so this and the readiness gate cannot name different
# variables for one provider; adding an API-key provider there reaches here.
_KEY_VARS: Final = ProviderCredentials().api_key_env_vars()


@final
class ApiKeyEnv:
    """A per-call API key and the environment variable its provider reads it from.

    A provider builds its SDK client from the environment, so a caller's own
    key reaches it only through that variable, set while the provider is built
    and restored afterwards. The variable is process-wide: the caller holds a
    lock across :meth:`applied` whenever :attr:`injects` is true.
    """

    __slots__ = ("_api_key", "_var")
    # None: the caller sent no key of its own, so the daemon's key stands.
    _api_key: str | None
    # None: the provider reads no API key (polly, say, espeak).
    _var: str | None

    def __new__(cls, provider_name: str, api_key: str | None) -> Self:
        self = super().__new__(cls)
        self._api_key = api_key or None
        self._var = _KEY_VARS.get(provider_name)
        return self

    @property
    def injects(self) -> bool:
        """Return whether :meth:`applied` changes the environment at all."""
        return self._api_key is not None and self._var is not None

    @contextmanager
    def applied(self) -> Generator[None]:
        """Set the key for the block, then restore whatever was there before."""
        if self._api_key is None or self._var is None:
            yield
            return
        var = self._var
        previous = os.environ.get(var)
        os.environ[var] = self._api_key
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = previous
