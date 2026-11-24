"""The durable worker_repos field: a list of resolved repository paths."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Self, cast, final

from punt_vox.config import ConfigStore

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
        raw = self._config.read_field(_FIELD, strict=True) or "[]"
        try:
            repos: object = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(_SHAPE) from exc
        if not isinstance(repos, list):
            raise ValueError(_SHAPE)
        items = cast("list[object]", repos)
        if not all(isinstance(p, str) for p in items):
            raise ValueError(_SHAPE)
        return {self.entry(str(p)) for p in items}

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

    def save(self, paths: set[Path]) -> tuple[str, ...]:
        """Write *paths* sorted and return them as strings.

        Each entry is a single-quoted Python literal, so the stored text never
        holds a double quote (the frontmatter refuses one) and ``literal_eval``
        reads an apostrophe back unchanged.

        A malformed config file is refused, never rebuilt around this field.

        Raises:
            ValueError: If the file has no closing fence to hold the field.
            OSError: If the file cannot be read or written.
        """
        repos = tuple(sorted(str(path) for path in paths))
        # A checked write never rebuilds a malformed file around this field;
        # the policy itself has no precondition, so the check is a no-op.
        self._config.write_field(
            _FIELD, self._literal(repos), check=lambda fields: None
        )
        return repos

    @staticmethod
    def _literal(repos: tuple[str, ...]) -> str:
        quoted = (
            "'" + repo.replace("\\", "\\\\").replace("'", "\\'") + "'" for repo in repos
        )
        return "[" + ", ".join(quoted) + "]"
