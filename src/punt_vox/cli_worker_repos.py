"""The ``Workers:`` line of ``vox status``: the allowlist, read from global config."""

from __future__ import annotations

from typing import Self, final

from punt_vox.config import ConfigStore
from punt_vox.voxd.workers.allowlist import RepoAllowlist


@final
class WorkerReposStatus:
    """Hold the allowlisted repositories, or the one reason they cannot be read."""

    __slots__ = ("_fault", "_repos")
    _repos: list[str]
    _fault: str | None  # None when the allowlist parsed.

    def __new__(cls) -> Self:
        """Read ``worker_repos`` from the daemon's global config once."""
        self = super().__new__(cls)
        self._repos, self._fault = [], None
        allowlist = RepoAllowlist(ConfigStore(ConfigStore.global_dir()))
        try:
            self._repos = sorted(str(path) for path in allowlist.paths())
        except ValueError as exc:
            self._fault = str(exc)
        return self

    @property
    def repos(self) -> list[str]:
        """Return the allowlisted paths, empty when unreadable."""
        return list(self._repos)

    def label(self) -> str:
        """Return the last path segments, or what to run, or the parse fault."""
        if self._fault is not None:
            return f"({self._fault})"
        if not self._repos:
            return "(none allowlisted; run vox worker allow <path>)"
        return ", ".join(sorted(repo.rsplit("/", 1)[-1] for repo in self._repos))
