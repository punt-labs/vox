"""Stand-ins for the ``punt_vox.dirs`` config-dir resolvers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def resolver_at(path: Path) -> Callable[[Path | None], Path]:
    """Return a config-dir resolver pinned at *path*."""

    def resolve(_start: Path | None = None) -> Path:
        return path

    return resolve
