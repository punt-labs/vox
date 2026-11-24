"""The ``Workers:`` line of ``vox status``: the daemon's allowlist, not local config."""

from __future__ import annotations

import asyncio
from typing import Self, final

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.daemon_status import DaemonState
from punt_vox.worker_client import WorkerClient

_UNREACHABLE = "daemon not reachable"


@final
class WorkerReposStatus:
    """Hold the daemon's allowlist, or the one reason it could not be read."""

    __slots__ = ("_error", "_repos")
    _repos: list[str] | None  # None: the daemon gave no allowlist.
    _error: str | None  # None when the daemon answered with a list.

    def __new__(cls, state: DaemonState) -> Self:
        """Ask a running daemon for its allowlist; any other state is unreachable."""
        self = super().__new__(cls)
        self._repos, self._error = None, _UNREACHABLE
        if state is DaemonState.RUNNING:
            self._repos, self._error = cls._ask()
        return self

    @staticmethod
    def _ask() -> tuple[list[str] | None, str | None]:
        try:
            repos = WorkerClient.paths(asyncio.run(WorkerClient.request("repos")))
        except VoxdConnectionError:
            return None, _UNREACHABLE
        except VoxdProtocolError as exc:
            return None, str(exc)
        return repos, None

    def fields(self) -> dict[str, list[str] | str | None]:
        """Return ``worker_repos`` and, when it is null, ``worker_repos_error``."""
        if self._error is not None:
            return {"worker_repos": None, "worker_repos_error": self._error}
        return {"worker_repos": self._repos}

    def label(self) -> str:
        """Return the last path segments, what to run, or why there is no answer."""
        if self._error == _UNREACHABLE:
            return f"unknown ({_UNREACHABLE})"
        if self._error is not None:
            return f"({self._error})"
        if not self._repos:
            return "(none allowlisted; run vox worker allow <path>)"
        return ", ".join(sorted(repo.rsplit("/", 1)[-1] for repo in self._repos))
