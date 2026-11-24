"""The durable worker_repos field: a list of resolved repository paths."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.config import ConfigStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.workers.repo_spellings import RepoSpellings

_FIELD = "worker_repos"
_SHAPE = "worker_repos must be a list of repository paths"


@final
class RepoAllowlist:
    """Read and write ``worker_repos`` as resolved paths, never raw text."""

    __slots__ = ("_config",)
    _config: ConfigStore

    def __new__(cls, config: ConfigStore) -> Self:
        """Bind the durable config the daemon owns."""
        self = super().__new__(cls)
        self._config = config
        return self

    def paths(self) -> set[Path]:
        """Return the allowlisted paths, resolved.

        The read is strict: a policy file that cannot be read is a fault,
        never an empty policy.

        Raises:
            ValueError: If the field is not a Python list literal of strings.
            OSError: If the config file exists but cannot be read.
            UnicodeDecodeError: If the config file is not UTF-8.
        """
        return {
            self.entry(p)
            for p in self._parse(self._config.read_field(_FIELD, strict=True))
        }

    def edit(self, change: Callable[[set[Path]], set[Path]]) -> tuple[str, ...]:
        """Replace the allowlist with ``change(current)`` under the file's lock.

        One locked read-modify-write: a writer that lands between the read
        and the write waits, so no entry is lost. Every stored entry is
        validated. Returns the stored paths, sorted.

        Raises:
            ValueError: If a stored entry is invalid or *change* refuses.
            OSError: If the file cannot be read or written, or has no closing
                fence to hold the field; a handler faults instead of blaming
                the client.
            UnicodeDecodeError: If the config file is not UTF-8.
        """

        def changed(entries: list[str]) -> list[str]:
            current = {self.entry(e) for e in entries}
            return sorted(str(path) for path in change(current))

        return self._rewrite(changed)

    def remove(self, named: RepoSpellings) -> tuple[str, ...]:
        """Drop every stored entry *named* names and return the rest, in order.

        Lenient on purpose, unlike :meth:`edit`: a stored entry that does
        not validate is matched by its text, so an operator can delete a
        hand-edited entry that would otherwise block every strict read.
        Every spelling is matched in the same locked update.

        Raises:
            ValueError: If no stored entry is named, or the field is not a
                list of strings.
            OSError: If the file cannot be read or written, or has no closing
                fence.
            UnicodeDecodeError: If the config file is not UTF-8.
        """

        def dropped(entries: list[str]) -> list[str]:
            remaining = [e for e in entries if not named.names(e)]
            if len(remaining) == len(entries):
                raise ValueError(f"not allowlisted: {named.resolved}")
            return remaining

        return self._rewrite(dropped)

    def _rewrite(self, change: Callable[[list[str]], list[str]]) -> tuple[str, ...]:
        """Store ``change(stored entries)`` in one locked read-modify-write.

        A malformed config file is refused with an ``OSError``, never
        rebuilt around this field.
        """
        written: tuple[str, ...] = ()

        def transform(raw: str | None) -> str:
            nonlocal written
            written = tuple(change(self._parse(raw)))
            return self._literal(written)

        # A checked write never rebuilds a malformed file around this field;
        # the policy itself has no precondition, so the check is a no-op.
        self._config.update_field(_FIELD, transform, check=lambda _fields: None)
        return written

    @staticmethod
    def _parse(raw: str | None) -> list[str]:
        """Return the stored strings of *raw*, an absent field reading as empty.

        Raises:
            ValueError: If *raw* is not a Python list literal of strings.
        """
        try:
            repos: object = ast.literal_eval(raw or "[]")
        except (ValueError, SyntaxError) as exc:
            raise ValueError(_SHAPE) from exc
        if not isinstance(repos, list):
            raise ValueError(_SHAPE)
        items = cast("list[object]", repos)
        if not all(isinstance(p, str) for p in items):
            raise ValueError(_SHAPE)
        return [str(p) for p in items]

    @staticmethod
    def entry(raw: str) -> Path:
        """Resolve one entry, refusing a blank, relative or root path by name.

        Raises:
            ValueError: If *raw* is blank after strip, holds a double quote,
                is relative after expanduser, or resolves to the filesystem root.
        """
        if not raw.strip():
            raise ValueError(f"worker_repos entry blank: {raw!r}")
        if '"' in raw:
            raise ValueError(f"worker_repos entry has a double quote: {raw!r}")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError(f"worker_repos entry not absolute: {raw!r}")
        path = path.resolve()
        if path.parent == path:
            raise ValueError(f"worker_repos entry the filesystem root: {raw!r}")
        return path

    @staticmethod
    def _literal(repos: tuple[str, ...]) -> str:
        """Render *repos* as single-quoted Python literals.

        The stored text never holds a double quote (the frontmatter refuses
        one) and ``literal_eval`` reads an apostrophe back unchanged.
        """
        quoted = (
            "'" + repo.replace("\\", "\\\\").replace("'", "\\'") + "'" for repo in repos
        )
        return "[" + ", ".join(quoted) + "]"
