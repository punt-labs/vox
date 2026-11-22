"""Inspect the daemon's curated hook context without starting a call."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.hook_context_client import HookContextClient


class CallContextCommand:
    """Present one session's live hook context."""

    @staticmethod
    def show(
        session: Annotated[str, typer.Option(help="Hook source's session ID.")],
    ) -> None:
        """Print the session's curated hook snapshot and sequence-gap report."""
        try:
            snapshot = asyncio.run(HookContextClient.snapshot(session))
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Hook context unavailable: {exc}", err=True)
            raise typer.Exit(1) from None
        typer.echo(json.dumps(snapshot, indent=2))
