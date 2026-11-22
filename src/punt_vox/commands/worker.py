"""Hidden worker CLI; lifecycle and policy live exclusively in voxd."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.worker_client import WorkerClient


@final
class WorkerCli:
    """Format daemon replies, without launching local workers."""

    __slots__ = ()

    @staticmethod
    def _run(operation: str, **fields: str) -> None:
        try:
            result = asyncio.run(WorkerClient.request(operation, **fields))
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Worker unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
        typer.echo(json.dumps(result, indent=2))

    @staticmethod
    def start(
        task: Annotated[str, typer.Option()],
        project: Annotated[str, typer.Option()],
        provider: str = "tmux",
        harness: str = "claude",
    ) -> None:
        """Start a worker in an allowlisted repository's new worktree."""
        WorkerCli._run(
            "start", task=task, project=project, provider=provider, harness=harness
        )

    @staticmethod
    def list_workers() -> None:
        """List workers and their retained work product paths."""
        WorkerCli._run("list")

    @staticmethod
    def stop(worker_id: str) -> None:
        """Stop a worker without deleting its worktree or branch."""
        WorkerCli._run("stop", worker_id=worker_id)

    @staticmethod
    def build_app() -> typer.Typer:
        """Register the hidden control group."""
        app = typer.Typer(help="Control background coding workers.")
        app.command("start")(WorkerCli.start)
        app.command("list")(WorkerCli.list_workers)
        app.command("stop")(WorkerCli.stop)
        return app
