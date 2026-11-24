"""Restrict worker edits to worktrees of explicitly configured repositories."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Self, final

from punt_vox.config import ConfigStore
from punt_vox.voxd.workers.allowlist import RepoAllowlist

logger = logging.getLogger(__name__)


@final
class WorkerRepoPolicy:
    """Resolve top levels before comparing against the durable allowlist."""

    __slots__ = ("_allowlist",)
    _allowlist: RepoAllowlist

    def __new__(cls, config: ConfigStore) -> Self:
        """Bind the durable policy source."""
        self = super().__new__(cls)
        self._allowlist = RepoAllowlist(config)
        return self

    def repos(self) -> tuple[str, ...]:
        """Return the allowlisted repository paths, sorted for a stable prompt."""
        return tuple(sorted(str(path) for path in self._allowlist.paths()))

    @staticmethod
    def _top_level(path: Path) -> Path:
        """Return the git top level containing *path*, or raise ``ValueError``."""
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise ValueError("git is not installed on the daemon host") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"git timed out reading {path}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            logger.warning("git rev-parse failed for %s: %s", path, detail)
            raise ValueError(f"not a git repository top level: {path}") from exc
        return Path(result.stdout.strip()).resolve()

    def allow(self, project: str) -> tuple[str, ...]:
        """Add a repository top level to the allowlist and return the new list.

        Raises:
            ValueError: If *project* is not a directory, not inside a git
                repository, or not that repository's top level.
        """
        path = RepoAllowlist.entry(project)
        if not path.is_dir():
            raise ValueError(f"not a directory: {path}")
        if self._top_level(path) != path:
            raise ValueError(f"not a git repository top level: {path}")
        return self._allowlist.save(self._allowlist.paths() | {path})

    def disallow(self, project: str) -> tuple[str, ...]:
        """Remove a repository from the allowlist and return the new list.

        Raises:
            ValueError: If the resolved path is not allowlisted.
        """
        path = RepoAllowlist.entry(project)
        allowed = self._allowlist.paths()
        if path not in allowed:
            raise ValueError(f"not allowlisted: {path}")
        return self._allowlist.save(allowed - {path})

    def resolve(self, project: str) -> Path:
        """Refuse subdirectories, unlisted repositories and malformed policy."""
        allowed = self._allowlist.paths()
        path = Path(project).expanduser()
        if not path.is_absolute():
            raise ValueError("Worker project must be an absolute path")
        path = path.resolve(strict=True)
        if path not in allowed:
            raise ValueError(
                f"Worker project is not allowlisted in worker_repos: {path}"
            )
        if self._top_level(path) != path:
            raise ValueError("Worker project must be a repository top level")
        return path

    @staticmethod
    def create(project: Path, worktree: Path, branch: str) -> None:
        """Keep the source checkout untouched and retain the new branch."""
        subprocess.run(
            ["git", "-C", str(project), "worktree", "add", "-b", branch, str(worktree)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
