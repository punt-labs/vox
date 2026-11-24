"""Decide which environment values ``vox daemon install`` writes to ``keys.env``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Self, final

from punt_vox.keys import HEADER_CARRIED_KEY_NAMES, PROVIDER_KEY_NAMES, parse_keys_env
from punt_vox.service.key_value_policy import ValuePolicy
from punt_vox.service.keys_env_report import KeyNotice, KeysEnvReport

# Values sent as HTTP header content (HEADER_CARRIED_KEY_NAMES) must be
# printable non-space ASCII, which also rejects a pasted smart quote. The AWS
# secret key only signs and never goes on the wire. The region does reach the
# wire, as part of the endpoint host name, but botocore validates it against its
# own host-label pattern before building a request, so it, the secret key, and
# the plain settings (profile, provider) need only stay on their own line.
_POLICIES = dict.fromkeys(HEADER_CARRIED_KEY_NAMES, ValuePolicy.header_safe())
_SETTING_POLICY = ValuePolicy.printable()
_CREDENTIALS = HEADER_CARRIED_KEY_NAMES | {"AWS_SECRET_ACCESS_KEY"}
# AWS signs with the key id, secret and session token together, so a stored
# pair is kept or replaced whole: never a stored pair with the shell's token.
_AWS_SET = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
)
_EMPTY = "empty in the environment"
_DIFFERS = "the environment's value differs (--rotate-keys replaces it)"
_HEADER = (
    "# vox provider keys — loaded by voxd at startup\n"
    "# Written by: vox daemon install\n"
    "# Edit with your normal editor — no sudo required.\n\n"
)


@final
class KeysEnvMerge:
    """The stored keys with the environment's added, and a notice per skip.

    Each value is stripped first, as both readers strip it. An empty or
    whitespace-only value never removes a stored key: a failed secret lookup
    exports ``KEY=""`` rather than unsetting it, and must not wipe a working
    credential. A value its :class:`ValuePolicy` refuses is skipped too, which
    stops any line break smuggling extra ``key=value`` lines into the file.

    A credential that differs from the stored one replaces it only when
    *rotate* is set: a stale key exported by the installing shell must not
    silently replace a working one. Otherwise the stored value is kept and
    noted. A setting (profile, region, provider) still follows the shell.
    """

    __slots__ = ("_held", "_merged", "_notices", "_rotated")

    _held: frozenset[str]
    _merged: dict[str, str]
    _notices: list[KeyNotice]
    _rotated: list[str]

    def __new__(
        cls, stored: Mapping[str, str], env: Mapping[str, str], *, rotate: bool = False
    ) -> Self:
        """Create a keys env merge; *rotate* lets env values replace stored ones."""
        self = super().__new__(cls)
        self._merged = {key: value for key, value in stored.items() if value}
        self._notices = []
        self._rotated = []
        taken = {key: env[key].strip() for key in PROVIDER_KEY_NAMES & env.keys()}
        self._held = self._held_over(self._merged, taken, rotate=rotate)
        for key in sorted(taken):
            self._take(key, taken[key])
        return self

    @classmethod
    def over_text(
        cls, text: str, env: Mapping[str, str], *, rotate: bool = False
    ) -> Self:
        """Return the merge of *env* onto the keys stored in keys.env *text*."""
        return cls(parse_keys_env(text), env, rotate=rotate)

    @property
    def merged(self) -> Mapping[str, str]:
        """Return every key to write, stored and new alike; no value is empty."""
        return self._merged

    @property
    def notices(self) -> tuple[KeyNotice, ...]:
        """Return one notice per environment key that was not written."""
        return tuple(self._notices)

    @property
    def rotated(self) -> tuple[str, ...]:
        """Return the keys whose stored value an environment value replaced."""
        return tuple(self._rotated)

    def text(self) -> str:
        """Return the keys.env content: the header, then sorted ``key=value`` lines."""
        body = "\n".join(f"{k}={v}" for k, v in sorted(self._merged.items()))
        return f"{_HEADER}{body}\n"

    def report(self, path: Path) -> KeysEnvReport:
        """Return the operator-facing account of this merge written to *path*."""
        return KeysEnvReport(path, tuple(self._notices), tuple(self._rotated))

    def _take(self, key: str, value: str) -> None:
        """Write *value* for *key*, or note why not and leave the stored value."""
        reason = self._reason_to_skip(key, value) or self._kept_over(key, value)
        if reason is None:
            self._merged[key] = value
        else:
            self._notices.append(KeyNotice(key, reason, key in self._merged))

    @staticmethod
    def _held_over(
        stored: Mapping[str, str], taken: Mapping[str, str], *, rotate: bool
    ) -> frozenset[str]:
        """Return the *taken* credentials kept off the file unless *rotate*."""
        if rotate:
            return frozenset()
        differing = {k for k, v in taken.items() if KeysEnvMerge._differs(stored, k, v)}
        return frozenset(differing | KeysEnvMerge._aws_drift(stored, taken, differing))

    @staticmethod
    def _aws_drift(
        stored: Mapping[str, str], taken: Mapping[str, str], differing: set[str]
    ) -> set[str]:
        """Return the AWS values in *taken* not stored, once any of them differs."""
        if not differing & _AWS_SET:
            return set()
        return {key for key in _AWS_SET & taken.keys() if stored.get(key) != taken[key]}

    @staticmethod
    def _differs(stored: Mapping[str, str], key: str, value: str) -> bool:
        """Return whether writable *value* would replace a *stored* credential."""
        return (
            key in _CREDENTIALS
            and stored.get(key, value) != value
            and not KeysEnvMerge._reason_to_skip(key, value)
        )

    def _kept_over(self, key: str, value: str) -> str | None:
        """Return why a differing stored credential stays; record it when rotated."""
        if key in self._held:
            return _DIFFERS
        if self._differs(self._merged, key, value):
            self._rotated.append(key)
        return None

    @staticmethod
    def _reason_to_skip(key: str, value: str) -> str | None:
        """Return why *value* is not written for *key*; ``None`` means write it."""
        if not value:
            return _EMPTY
        refusal = _POLICIES.get(key, _SETTING_POLICY).refusal(value)
        return None if refusal is None else f"refused: {refusal}"
