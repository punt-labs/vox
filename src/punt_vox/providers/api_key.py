"""An API key checked before it can reach an HTTP header.

A key pasted with a trailing newline -- the usual shape of a ``keys.env`` line
or a ``--api-key-file`` read -- is a header value httpx refuses to send, and
httpx's refusal quotes the value. Checking the key where it enters voxd turns
that into a :class:`MalformedApiKeyError` that names where the key came from
and never the key.
"""

from __future__ import annotations

from typing import Self, final

from punt_vox.providers.credentials import ProviderCredentials
from punt_vox.providers.provider_faults import ProviderConfigError
from punt_vox.types import HealthCheck

__all__ = ["ApiKeyCheck", "MalformedApiKeyError"]


class MalformedApiKeyError(ProviderConfigError):
    """An API key holds a character no HTTP header may carry.

    A :class:`ProviderConfigError`, so it is permanent everywhere one is -- the
    wire sends it verbatim, music stops filling, a call ends -- and its message
    names the key's source, never its value.
    """


@final
class ApiKeyCheck:
    """Reject a key from one source when it holds a control character."""

    __slots__ = ("_source",)
    # What a message calls the key: the variable it was read from, or the wire
    # field it arrived in.
    _source: str

    def __new__(cls, source: str) -> Self:
        self = super().__new__(cls)
        self._source = source
        return self

    @classmethod
    def for_provider(cls, provider: str) -> Self:
        """Return the check for *provider*'s key variable, as the readiness gate
        names it -- the one place a provider's key variable is spelled."""
        return cls(ProviderCredentials().api_key_env_vars()[provider])

    @property
    def source(self) -> str:
        """Return where the key comes from: a variable name, or a wire field."""
        return self._source

    def require(
        self, key: str | None
    ) -> str | None:  # None passes: a missing key is the readiness gate's verdict
        """Return *key* unchanged, or raise when it cannot go in a header.

        Raises:
            MalformedApiKeyError: *key* holds a control character.
        """
        if key is not None and self.is_malformed(key):
            raise MalformedApiKeyError(
                f"{self._source} contains a control character; check it for "
                "stray whitespace or newlines"
            )
        return key

    def report(self, label: str, key: str | None) -> HealthCheck:
        """Return *label*'s API-key health line: absent, malformed, or set.

        *label* names the provider as a person reads it ("ElevenLabs").
        """
        prefix = f"{label} API key"
        if not key:
            return HealthCheck(
                passed=False, message=f"{prefix}: not set (export {self._source})"
            )
        if self.is_malformed(key):
            return HealthCheck(
                passed=False,
                message=(
                    f"{prefix}: {self._source} contains a control character "
                    "(a stray newline?)"
                ),
            )
        return HealthCheck(passed=True, message=f"{prefix}: set")

    @staticmethod
    def is_malformed(key: str) -> bool:
        """Return whether *key* holds a C0 control character or DEL."""
        return any(ord(char) < 0x20 or ord(char) == 0x7F for char in key)
