"""The deny-list of paths a call agent must never read, because its replies are spoken.

A read-only tool allowlist stops writes; it does not stop disclosure, and on
a call disclosure is audible: a secret the agent reads can be narrated aloud
to anyone in the room. :class:`SecretPathPolicy` names the paths that are off
limits -- anything outside the repository root, a fixed set of well-known
secret files, and every secret-looking pattern in the repo's own
``.gitignore`` -- and answers one question about a candidate path: is it
denied?

It is enforced in three layers. The system prompt states it as a hard rule
(:meth:`SecretPathPolicy.prompt_clause`). The pi-side guard extension,
configured from :meth:`SecretPathPolicy.guard_env`, blocks a denied tool call
before it executes -- the layer that actually prevents the read. And the
process host re-checks every *executed* call on the RPC stream
(:class:`~.tool_screen.ToolScreen`), ending the call if a denied read ever
ran anyway.

Patterns follow ``.gitignore`` shape, matched case-insensitively: a pattern
with no inner slash matches any path component at any depth (``.env*``
denies ``config/.env.local``); one with a slash is anchored to the repo root
(``.punt-labs/**/*.local.*``); everything beneath a denied component is
denied with it.
"""

from __future__ import annotations

import json
import os
import re
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.pi_path import PiPath

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
    # Secret-shaped extensions only: ``credentials.*`` would deny source code
    # such as vox's own providers/credentials.py.
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    "credentials.toml",
    "credentials.ini",
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

_GLOB_CHARS = re.compile(r"[*?\[\]{}!]")


@final
class SecretPathPolicy:
    """Decide whether a path the call agent names is on the secret deny-list."""

    __slots__ = ("_patterns", "_real_root", "_root")
    _root: Path
    _real_root: Path
    _patterns: tuple[str, ...]

    def __new__(cls, root: Path, patterns: Iterable[str] = ()) -> Self:
        self = super().__new__(cls)
        self._root = Path(os.path.normpath(root.expanduser().absolute()))
        self._real_root = self._root.resolve()
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

    def denies(self, candidate: str, *, cwd: Path) -> bool:
        """Return whether the path *candidate* names, from *cwd*, is denied.

        The path is normalized as pi normalizes it (:class:`~.pi_path.PiPath`),
        then denied when it resolves -- symlinks followed -- outside the repo
        root, or when it or its resolved form matches a pattern,
        case-insensitively.
        """
        lexical = PiPath.resolve(candidate, cwd)
        real = lexical.resolve(strict=False)
        if not real.is_relative_to(self._real_root):
            return True
        forms = [real.relative_to(self._real_root).parts]
        if lexical.is_relative_to(self._root):
            forms.append(lexical.relative_to(self._root).parts)
        return any(self._matches(parts) for parts in forms)

    def denies_glob(self, glob: str) -> bool:
        """Return whether a grep ``glob`` or find ``pattern`` aims at secrets.

        Checked as written and with its wildcard characters removed, so
        ``*.env*`` is caught by ``.env*`` as surely as ``.env`` itself is.
        """
        parts = tuple(part for part in glob.split("/") if part)
        stripped = tuple(_GLOB_CHARS.sub("", part) for part in parts)
        return self._matches(parts) or self._matches(stripped)

    def guard_env(self) -> dict[str, str]:
        """Return the variables that configure the pi-side secret guard."""
        return {
            "VOX_CALL_GUARD_ROOT": str(self._real_root),
            "VOX_CALL_GUARD_PATTERNS": json.dumps(self._patterns),
        }

    def prompt_clause(self) -> str:
        """Return the system-prompt rule stating the deny-list."""
        listing = ", ".join(self._patterns)
        return (
            "HARD RULE: stay inside the repository, and never read, grep, find, "
            f"or list any path matching these patterns, at any depth: {listing}. "
            "Everything you read is spoken aloud in a room, so a secret you "
            "read is a secret disclosed. If asked for one of these files, "
            "refuse and say why; the tools will refuse it too."
        )

    def _matches(self, parts: tuple[str, ...]) -> bool:
        folded = tuple(part.lower() for part in parts)
        return any(self._pattern_matches(pattern, folded) for pattern in self._patterns)

    @staticmethod
    def _pattern_matches(pattern: str, parts: tuple[str, ...]) -> bool:
        body = pattern.strip("/").lower()
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
