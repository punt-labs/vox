"""Decide which environment values ``vox daemon install`` writes to ``keys.env``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Self, final

from punt_vox.keys import PROVIDER_KEY_NAMES, parse_keys_env
from punt_vox.service.key_admission import AwsCredentials, KeyAdmission
from punt_vox.service.keys_env_report import KeyNotice, KeysEnvReport

_DIFFERS = "the environment's value differs (--rotate-keys replaces it)"
_AWS_DIFFERS = "the stored AWS key pair differs (--rotate-keys replaces it)"
_HEADER = (
    "# vox provider keys — loaded by voxd at startup\n"
    "# Written by: vox daemon install\n"
    "# Edit with your normal editor — no sudo required.\n\n"
)


@final
class KeysEnvMerge:
    """The stored keys with the environment's added, and a notice per skip.

    Each value is stripped first, as both readers strip it, then written only
    if :class:`KeyAdmission` admits it: an empty or refused value never
    removes a stored key.

    A credential that differs from the stored one replaces it only when
    *rotate* is set: a stale key exported by the installing shell must not
    silently replace a working one. Otherwise the stored value is kept and
    noted. A setting (profile, region, provider) still follows the shell. The
    AWS credentials are kept or replaced as one set (see :class:`AwsCredentials`).
    """

    __slots__ = ("_aws", "_held", "_merged", "_notices", "_removed", "_rotated")

    _aws: AwsCredentials
    _held: frozenset[str]
    _merged: dict[str, str]
    _notices: list[KeyNotice]
    _removed: tuple[str, ...]
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
        self._aws = AwsCredentials(self._merged, taken)
        self._held = self._held_over(taken, rotate=rotate)
        for key in sorted(taken):
            self._take(key, taken[key])
        self._removed = self._aws.stale(self._merged)
        self._drop(self._removed)
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

    @property
    def removed(self) -> tuple[str, ...]:
        """Return the stored AWS keys dropped with the key pair that replaced them."""
        return self._removed

    def text(self) -> str:
        """Return the keys.env content: the header, then sorted ``key=value`` lines."""
        body = "\n".join(f"{k}={v}" for k, v in sorted(self._merged.items()))
        return f"{_HEADER}{body}\n"

    def report(self, path: Path) -> KeysEnvReport:
        """Return the operator-facing account of this merge written to *path*."""
        return KeysEnvReport(
            path, tuple(self._notices), tuple(self._rotated), self._removed
        )

    def _take(self, key: str, value: str) -> None:
        """Write *value* for *key*, or note why not and leave the stored value."""
        reason = KeyAdmission.refusal(key, value) or self._kept_over(key, value)
        if reason is None:
            self._merged[key] = value
        else:
            kept = key in self._merged
            aws_stay = not kept and self._aws.covers(key)
            self._notices.append(KeyNotice(key, reason, kept, aws_stay))

    def _held_over(self, taken: Mapping[str, str], *, rotate: bool) -> frozenset[str]:
        """Return the *taken* credentials kept off the file unless *rotate*."""
        if rotate:
            return frozenset()
        differing = {
            k for k, v in taken.items() if KeyAdmission.replaces(self._merged, k, v)
        }
        return frozenset(differing | self._aws.drift(differing))

    def _kept_over(self, key: str, value: str) -> str | None:
        """Return why a differing stored credential stays; record it when rotated."""
        replaces = KeyAdmission.replaces(self._merged, key, value)
        if key in self._held:
            return _DIFFERS if replaces else _AWS_DIFFERS
        if replaces:
            self._rotated.append(key)
        return None

    def _drop(self, keys: tuple[str, ...]) -> None:
        """Remove *keys* from the merged keys."""
        for key in keys:
            del self._merged[key]
