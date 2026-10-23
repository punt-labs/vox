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
_EMPTY = "empty in the environment"
_HEADER = (
    "# vox provider keys — loaded by voxd at startup\n"
    "# Written by: vox daemon install\n"
    "# Edit with your normal editor — no sudo required.\n\n"
)


@final
class KeysEnvMerge:
    """The stored keys overlaid with the environment's, and a notice per skip.

    Each value is stripped first, as both readers strip it. An empty or
    whitespace-only value never removes a stored key: a failed secret lookup
    exports ``KEY=""`` rather than unsetting it, and must not wipe a working
    credential. A value its :class:`ValuePolicy` refuses is skipped too, which
    stops any line break smuggling extra ``key=value`` lines into the file.
    """

    __slots__ = ("_merged", "_notices")

    _merged: dict[str, str]
    _notices: list[KeyNotice]

    def __new__(cls, stored: Mapping[str, str], env: Mapping[str, str]) -> Self:
        self = super().__new__(cls)
        self._merged = {key: value for key, value in stored.items() if value}
        self._notices = []
        for key in sorted(PROVIDER_KEY_NAMES & env.keys()):
            self._take(key, env[key].strip())
        return self

    @classmethod
    def over_text(cls, text: str, env: Mapping[str, str]) -> Self:
        """Return the merge of *env* onto the keys stored in keys.env *text*."""
        return cls(parse_keys_env(text), env)

    @property
    def merged(self) -> Mapping[str, str]:
        """Return every key to write, stored and new alike; no value is empty."""
        return self._merged

    @property
    def notices(self) -> tuple[KeyNotice, ...]:
        """Return one notice per environment key that was not written."""
        return tuple(self._notices)

    def text(self) -> str:
        """Return the keys.env content: the header, then sorted ``key=value`` lines."""
        body = "\n".join(f"{k}={v}" for k, v in sorted(self._merged.items()))
        return f"{_HEADER}{body}\n"

    def report(self, path: Path) -> KeysEnvReport:
        """Return the operator-facing account of this merge written to *path*."""
        return KeysEnvReport(path, self.notices)

    def _take(self, key: str, value: str) -> None:
        """Write *value* for *key*, or note why not and leave the stored value."""
        reason = self._reason_to_skip(key, value)
        if reason is None:
            self._merged[key] = value
        else:
            self._notices.append(KeyNotice(key, reason, key in self._merged))

    @staticmethod
    def _reason_to_skip(key: str, value: str) -> str | None:
        """Return why *value* is not written for *key*; ``None`` means write it."""
        if not value:
            return _EMPTY
        refusal = _POLICIES.get(key, _SETTING_POLICY).refusal(value)
        return None if refusal is None else f"refused: {refusal}"
