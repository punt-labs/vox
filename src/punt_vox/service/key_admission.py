"""Decide whether ``vox daemon install`` may write environment values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self, final

from punt_vox.keys import HEADER_CARRIED_KEY_NAMES
from punt_vox.service.key_value_policy import ValuePolicy

# Values sent as HTTP header content (HEADER_CARRIED_KEY_NAMES) must be
# printable non-space ASCII, which also rejects a pasted smart quote. The AWS
# secret key only signs and never goes on the wire. The region does reach the
# wire, as part of the endpoint host name, but botocore validates it against its
# own host-label pattern before building a request, so it, the secret key, and
# the plain settings (profile, provider) need only stay on their own line.
_POLICIES = dict.fromkeys(HEADER_CARRIED_KEY_NAMES, ValuePolicy.header_safe())
_SETTING_POLICY = ValuePolicy.printable()
_CREDENTIALS = HEADER_CARRIED_KEY_NAMES | {"AWS_SECRET_ACCESS_KEY"}
_EMPTY = "empty in the environment"
_AWS_PAIR = frozenset({"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"})
AWS_SIGNING_KEYS = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
)


@final
class KeyAdmission:
    """Whether a stripped environment value may go into ``keys.env``, and why not.

    An empty value is never written, so a failed secret lookup exporting
    ``KEY=""`` cannot wipe a working credential; nor is a value its
    :class:`ValuePolicy` refuses, which stops any line break smuggling extra
    ``key=value`` lines into the file.
    """

    __slots__ = ()

    @staticmethod
    def refusal(key: str, value: str) -> str | None:
        """Return why *value* is not written for *key*; ``None`` means write it."""
        if not value:
            return _EMPTY
        refusal = _POLICIES.get(key, _SETTING_POLICY).refusal(value)
        return None if refusal is None else f"refused: {refusal}"

    @staticmethod
    def replaces(stored: Mapping[str, str], key: str, value: str) -> bool:
        """Return whether writable *value* would replace a *stored* credential."""
        return (
            key in _CREDENTIALS
            and stored.get(key, value) != value
            and KeyAdmission.refusal(key, value) is None
        )


@final
class AwsCredentials:
    """The stored and shell AWS key id, secret and session token of one merge.

    AWS signs with all three together, so a merge keeps or replaces them as
    one set: a kept pair never takes the shell's session token, and a pair the
    shell replaces never keeps the stored token beside it.
    """

    __slots__ = ("_stored", "_taken")

    _stored: dict[str, str]
    _taken: dict[str, str]

    def __new__(cls, stored: Mapping[str, str], taken: Mapping[str, str]) -> Self:
        """Create the AWS credentials of a merge of *taken* onto *stored*."""
        self = super().__new__(cls)
        self._stored = {k: v for k, v in stored.items() if k in AWS_SIGNING_KEYS}
        self._taken = {k: v for k, v in taken.items() if k in AWS_SIGNING_KEYS}
        return self

    def covers(self, key: str) -> bool:
        """Return whether *key* belongs to a stored AWS credential set."""
        return key in AWS_SIGNING_KEYS and bool(self._stored)

    def drift(self, differing: set[str]) -> set[str]:
        """Return the shell's AWS values not stored, once any of *differing* is."""
        if not differing & AWS_SIGNING_KEYS:
            return set()
        return {k for k, v in self._taken.items() if self._stored.get(k) != v}

    def stale(self, merged: Mapping[str, str]) -> tuple[str, ...]:
        """Return the stored AWS keys left behind once *merged* replaced the pair.

        Only a whole pair written from the shell drops them: a lone new key id
        or secret leaves every stored AWS key where it is.
        """
        written = {k for k, v in self._taken.items() if merged.get(k) == v}
        kept = all(self._stored.get(k) == merged.get(k) for k in _AWS_PAIR)
        if kept or not written >= _AWS_PAIR:
            return ()
        return tuple(sorted(self._stored.keys() - written))
