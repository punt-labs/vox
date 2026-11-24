"""The worker allowlist CLI: allow, disallow and repos, all answered by voxd."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, cast, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.worker_client import WorkerClient

JsonFlag = Annotated[bool, typer.Option("--json", help="Print a JSON array.")]


@final
class WorkerReposCli:
    """Print the daemon's allowlist, one path per line or as a JSON array."""

    __slots__ = ()

    @staticmethod
    def _paths(operation: str, *, as_json: bool, **fields: str) -> None:
        try:
            result = asyncio.run(WorkerClient.request(operation, **fields))
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"vox: worker {operation}: {exc}", err=True)
            raise typer.Exit(1) from None
        paths = [str(p) for p in cast("list[object]", result)]
        if as_json:
            typer.echo(json.dumps(paths))
            return
        for path in paths:
            typer.echo(path)

    @staticmethod
    def allow(path: Path, *, as_json: JsonFlag = False) -> None:
        """Allow workers in a repository; PATH must be a git top level."""
        WorkerReposCli._paths(
            "allow", as_json=as_json, project=str(path.expanduser().resolve())
        )

    @staticmethod
    def disallow(path: Path, *, as_json: JsonFlag = False) -> None:
        """Remove a repository from the worker allowlist."""
        WorkerReposCli._paths(
            "disallow", as_json=as_json, project=str(path.expanduser().resolve())
        )

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
