"""The deny-list of paths a call agent must never read, because its replies are spoken.

A read-only tool allowlist stops writes; it does not stop disclosure, and on
a call disclosure is audible: a secret the agent reads can be narrated aloud
to anyone in the room. :class:`SecretPathPolicy` names the paths that are off
limits -- a fixed set of well-known secret files plus every secret-looking
pattern in the repo's own ``.gitignore`` -- and answers one question about a
candidate path: is it denied?

It is enforced twice. The deny-list is written into the agent's system prompt
as a hard rule (:meth:`SecretPathPolicy.prompt_clause`), so a cooperative
model declines up front; and the process host screens every tool call on the
RPC stream against :meth:`SecretPathPolicy.denies` and aborts the turn on the
first hit, so a model that ignores the rule still never gets its read spoken.

Patterns follow ``.gitignore`` shape: a pattern with no inner slash matches
any path component at any depth (``.env*`` denies ``config/.env.local``);
one with a slash is anchored to the repo root (``.punt-labs/**/*.local.*``);
a trailing slash marks a directory, and everything beneath a denied
component is denied with it.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["SecretPathPolicy"]

# Well-known secret locations, denied in every repo whatever its .gitignore
# says. ``auth.json`` is pi's own credential store (``~/.pi/agent``), and
# ``keys.env`` is voxd's -- the agent must not read either of its hosts' keys.
_BUILTIN_PATTERNS: tuple[str, ...] = (
    ".env*",
    "*.env",
    "keys.env",
    "auth.json",
    ".mcp.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".ssh/",
    ".aws/",
    ".gnupg/",
)

# A .gitignore line is taken as a secret pattern when it names one of these.
# Over-matching only denies a little more than strictly needed, which is the
# safe direction for a list whose whole purpose is to fail closed.
_SECRET_HINT = re.compile(r"env|secret|key|token|cred|passw|\.local", re.IGNORECASE)


@final
class SecretPathPolicy:
    """Decide whether a path the call agent names is on the secret deny-list."""

    __slots__ = ("_patterns", "_root")
    _root: Path
    _patterns: tuple[str, ...]

    def __new__(cls, root: Path, patterns: Iterable[str] = ()) -> Self:
        self = super().__new__(cls)
        self._root = Path(os.path.normpath(root.expanduser().absolute()))
        self._patterns = tuple(dict.fromkeys((*_BUILTIN_PATTERNS, *patterns)))
        return self

    @classmethod
    def for_repo(cls, root: Path) -> Self:
        """Build *root*'s policy: the built-ins plus its ``.gitignore`` secrets."""
        gitignore = root / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.is_file() else []
        return cls(root, cls._secret_lines(lines))

    @property
    def patterns(self) -> tuple[str, ...]:
        return self._patterns

    def denies(self, candidate: str) -> bool:
        """Return whether *candidate* (a path or glob the agent named) is denied.

        Checked both as written and with symlinks resolved, so an innocent
        name linked to ``.env`` is denied too.
        """
        if not candidate.strip():
            return False
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = self._root / path
        lexical = Path(os.path.normpath(path))
        return any(
            self._matches(self._parts(variant))
            for variant in (lexical, lexical.resolve(strict=False))
        )

    def prompt_clause(self) -> str:
        """Return the system-prompt rule stating the deny-list."""
        listing = ", ".join(self._patterns)
        return (
            "HARD RULE: never read, grep, find, or list any path matching "
            f"these patterns, at any depth: {listing}. Everything you read is "
            "spoken aloud in a room, so a secret you read is a secret "
            "disclosed. If asked for one of these files, refuse and say why. "
            "Any attempt to touch one ends the call."
        )

    def _parts(self, path: Path) -> tuple[str, ...]:
        """Return *path*'s components, relative to the repo root when inside it."""
        if path.is_relative_to(self._root):
            return path.relative_to(self._root).parts
        return path.parts[1:]

    def _matches(self, parts: tuple[str, ...]) -> bool:
        return any(self._pattern_matches(pattern, parts) for pattern in self._patterns)

    @staticmethod
    def _pattern_matches(pattern: str, parts: tuple[str, ...]) -> bool:
        body = pattern.strip("/")
        if "/" not in body:
            return any(fnmatchcase(part, body) for part in parts)
        return any(
            PurePosixPath(*parts[:end]).full_match(body)
            for end in range(1, len(parts) + 1)
        )

    @staticmethod
    def _secret_lines(lines: Iterable[str]) -> list[str]:
        """Return the ``.gitignore`` patterns that look like secret locations."""
        return [
            stripped
            for line in lines
            if (stripped := line.strip())
            and not stripped.startswith(("#", "!"))
            and _SECRET_HINT.search(stripped)
        ]
