"""Restrict worker edits to worktrees of explicitly configured repositories."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Self, cast, final

from punt_vox.config import ConfigStore


@final
class WorkerRepoPolicy:
    """Resolve top levels before comparing against the durable allowlist."""

    __slots__ = ("_config",)
    _config: ConfigStore

    def __new__(cls, config: ConfigStore) -> Self:
        """Bind the durable policy source."""
        self = super().__new__(cls)
        self._config = config
        return self

    @staticmethod
    def _allowed(raw: str) -> set[Path]:
        try:
            repos: object = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as exc:
            raise ValueError("worker_repos must be a list of repository paths") from exc
        if not isinstance(repos, list):
            raise ValueError("worker_repos must be a list of repository paths")
        paths = cast("list[object]", repos)
        if not all(isinstance(p, str) for p in paths):
            raise ValueError("worker_repos must be a list of repository paths")
        return {Path(str(p)).expanduser().resolve() for p in paths}

    def resolve(self, project: str) -> Path:
        """Refuse subdirectories, unlisted repositories and malformed policy."""
        allowed = self._allowed(self._config.read_field("worker_repos") or "[]")
        path = Path(project).expanduser().resolve(strict=True)
        if path not in allowed:
            raise ValueError(
                f"Worker project is not allowlisted in worker_repos: {path}"
            )
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if Path(result.stdout.strip()).resolve() != path:
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
