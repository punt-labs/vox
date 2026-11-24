"""Vox config as seen from a working directory, resolved at each access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, final

from punt_vox import dirs
from punt_vox.config import ConfigStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from punt_vox.config import VoxConfig

__all__ = ["CommandStore", "RepoConfig"]


class CommandStore(Protocol):
    """The config reads and writes the command layer makes."""

    def read(self) -> VoxConfig:
        """Return every config field."""
        ...

    def write_field(
        self,
        key: str,
        value: str,
        *,
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Write one field."""
        ...

    def write_fields(
        self,
        updates: dict[str, str],
        *,
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Write several fields in one write."""
        ...


@final
class RepoConfig:
    """Read config where reads resolve; write it where writes land.

    Reads follow :func:`~punt_vox.dirs.find_config_dir`, so a nested repo with
    no config of its own sees its workspace's.  Writes follow
    :func:`~punt_vox.dirs.writable_config_dir`, which gives that repo its own
    seeded copy only when a write actually happens.  Resolving on every call
    means a holder that outlives a write (the panel) reads the new copy next.
    """

    __slots__ = ("_durable_only", "_start")

    _start: Path | None
    _durable_only: bool

    def __new__(cls, start: Path | None = None, *, durable_only: bool = False) -> Self:
        """Resolve from *start* (default: cwd at each access)."""
        self = super().__new__(cls)
        self._start = start
        self._durable_only = durable_only
        return self

    def read(self) -> VoxConfig:
        """Return the config this directory reads."""
        return self._store(dirs.find_config_dir(self._start)).read()

    def write_field(
        self,
        key: str,
        value: str,
        *,
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Write one field where a write from this directory lands."""
        self._writable().write_field(key, value, check=check)

    def write_fields(
        self,
        updates: dict[str, str],
        *,
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Write *updates* in one write where a write from this directory lands."""
        self._writable().write_fields(updates, check=check)

    def _writable(self) -> ConfigStore:
        return self._store(dirs.writable_config_dir(self._start))

    def _store(self, config_dir: Path | None) -> ConfigStore:
        return ConfigStore(config_dir, durable_only=self._durable_only)
