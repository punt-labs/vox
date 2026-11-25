"""The worker allowlist CLI: allow, disallow and repos, all answered by voxd."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.repo_spellings import RepoSpellings
from punt_vox.worker_client import WorkerClient

JsonFlag = Annotated[bool, typer.Option("--json", help="Print a JSON array.")]


@final
class WorkerReposCli:
    """Print the daemon's allowlist, one path per line or as a JSON array."""

    __slots__ = ()

    @staticmethod
    def _paths(operation: str, *, as_json: bool, **fields: str) -> None:
        try:
            paths = WorkerClient.paths(
                asyncio.run(WorkerClient.request(operation, **fields))
            )
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"vox: worker {operation}: {exc}", err=True)
            raise typer.Exit(1) from None
        if as_json:
            typer.echo(json.dumps(paths))
            return
        for path in paths:
            typer.echo(path)

    @staticmethod
    def _present(operation: str, path: str) -> str:
        """Refuse a blank PATH by name: resolved, it would name the cwd."""
        if not path.strip():
            typer.echo(f"vox: worker {operation}: PATH must not be blank", err=True)
            raise typer.Exit(2)
        return path

    @staticmethod
    def _resolved(path: str) -> str | None:  # None: the client cannot resolve it.
        """Resolve PATH in the caller's directory, or ``None`` when that fails.

        ``~user`` for an unknown account, or a path resolution the filesystem
        refuses, has no resolved form on this host.
        """
        try:
            return str(Path(path).expanduser().resolve())
        except (RuntimeError, OSError):
            return None

    @staticmethod
    def allow(path: str, *, as_json: JsonFlag = False) -> None:
        """Allow workers in a repository; PATH must be a git top level."""
        typed = WorkerReposCli._present("allow", path)
        resolved = WorkerReposCli._resolved(typed)
        if resolved is None:
            typer.echo(f"vox: worker allow: cannot resolve PATH: {typed}", err=True)
            raise typer.Exit(1)
        WorkerReposCli._paths("allow", as_json=as_json, project=resolved)

    @staticmethod
    def disallow(path: str, *, as_json: JsonFlag = False) -> None:
        """Remove a repository from the worker allowlist.

        PATH is resolved the way allow resolved it, so the spelling that
        allowed a repository disallows it. The spelling as typed rides along
        when it differs, so a stored entry that does not resolve (one written
        by hand) can still be removed by typing it exactly.
        """
        typed = WorkerReposCli._present("disallow", path)
        resolved = WorkerReposCli._resolved(typed)
        # No resolved form: the typed text is the only spelling the daemon
        # can match, so it goes as the project itself.
        named = (
            RepoSpellings(typed) if resolved is None else RepoSpellings(resolved, typed)
        )
        WorkerReposCli._paths("disallow", as_json=as_json, **named.fields())

    @staticmethod
    def repos(*, as_json: JsonFlag = False) -> None:
        """List the repositories workers may start in, one per line."""
        WorkerReposCli._paths("repos", as_json=as_json)

    @staticmethod
    def register(app: typer.Typer) -> None:
        """Attach the three allowlist commands to the worker group."""
        app.command("allow")(WorkerReposCli.allow)
        app.command("disallow")(WorkerReposCli.disallow)
        app.command("repos")(WorkerReposCli.repos)
