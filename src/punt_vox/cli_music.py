"""The consume-only ``vox music`` CLI -- play and manage saved audio albums.

The CLI never authors (no LLM, no generation): it lists, plays a Selection,
advances, and shows status. :class:`MusicCli` is a humble object testable with an
in-memory gateway and formatter; every read and command crosses to ``voxd`` via a
:class:`ProgramGateway`. The daemon owns the catalog -- the CLI never touches the
store directly (R2 layering fix).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, NoReturn, Self, final

import typer
from websockets.exceptions import WebSocketException

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_gateway import ClientProgramGateway
from punt_vox.client_sync import VoxClientSync
from punt_vox.output_formatter import OutputFormatter
from punt_vox.program_gateway import ProgramGateway
from punt_vox.types_programs.control import SelectionRequest
from punt_vox.types_programs.status import ProgramStatus

__all__ = ["MusicCli", "build_music_app"]

# A client error, a raw WebSocket failure (stale-token handshake / mid-request
# close -- matching the MCP tools, F1), or a bad name (ValueError) fails cleanly.
_GATEWAY_ERRORS = (
    VoxdConnectionError,
    VoxdProtocolError,
    WebSocketException,
    OSError,
    ValueError,
)


@final
class MusicCli:
    """The consume-only music command implementations (a humble object)."""

    __slots__ = ("_formatter", "_gateway_factory")
    _formatter: OutputFormatter
    _gateway_factory: Callable[[], ProgramGateway]

    def __new__(
        cls,
        formatter: OutputFormatter,
        gateway_factory: Callable[[], ProgramGateway] | None = None,
    ) -> Self:
        self = super().__new__(cls)
        self._formatter = formatter
        self._gateway_factory = gateway_factory or cls._default_gateway
        return self

    @staticmethod
    def _default_gateway() -> ProgramGateway:
        """Build the production gateway -- a fresh WebSocket client per command."""
        return ClientProgramGateway(VoxClientSync())

    @staticmethod
    def _fail(message: str) -> NoReturn:
        """Print an error to stderr and exit non-zero -- a clean CLI failure."""
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1)

    def list_programs(self) -> None:
        """List catalog albums via the daemon, with their ready/total counts."""
        try:
            albums = self._gateway_factory().catalog()
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        if not albums:
            self._formatter.emit({"programs": []}, "No saved albums.")
            return
        entries = [
            {
                "id": a.id,
                "style": a.style,
                "vibe": a.vibe,
                "name": a.name,
                "ready": a.ready,
                "total": a.total,
            }
            for a in albums
        ]
        listing = "\n".join(f"  {a.display_line()}" for a in albums)
        self._formatter.emit(
            {"programs": entries}, f"{len(albums)} saved album(s):\n{listing}"
        )

    def play(
        self,
        style: Annotated[
            str | None, typer.Argument(help="Style tag to replay, e.g. 'trance'.")
        ] = None,
        vibe: Annotated[
            str | None, typer.Argument(help="Vibe tag to replay, e.g. 'calm'.")
        ] = None,
        name: Annotated[
            str | None, typer.Option("--name", help="Curated album name to replay.")
        ] = None,
        album_id: Annotated[
            str | None, typer.Option("--id", help="Exact album id to replay.")
        ] = None,
    ) -> None:
        """Replay a Selection resolved by style/vibe/name tags or an exact id."""
        request = SelectionRequest(style=style, vibe=vibe, name=name, id=album_id)
        try:
            outcome = self._gateway_factory().select(request)
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(
            {"music": "play", "applied": outcome.applied},
            outcome.display("Playing selection."),
        )

    def advance(self) -> None:
        """Advance the active source to another Part."""
        try:
            outcome = self._gateway_factory().advance()
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(
            {"music": "next", "applied": outcome.applied},
            outcome.display("Advancing to another part."),
        )

    def status(self) -> None:
        """Show the active source's authoritative status."""
        try:
            report = self._gateway_factory().status()
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(report.to_dict(), self._render_status(report))

    @staticmethod
    def _render_status(status: ProgramStatus) -> str:
        """Render a ProgramStatus as a short human block for the CLI."""
        if status.is_idle:
            return "Nothing playing."
        now = status.now_playing
        where = f"playing {now.index} of {now.of}" if now is not None else "stopped"
        head = status.name.value if status.name is not None else status.format.label
        lines = [f"{head} [{status.format.label}] — {where} ({status.mode.value})"]
        if status.generation.last_error is not None:
            lines.append(f"  error: {status.generation.last_error}")
        lines += [f"  part {f.index} failed: {f.reason}" for f in status.failed_parts]
        return "\n".join(lines)


def build_music_app(formatter: OutputFormatter) -> typer.Typer:
    """Return the ``vox music`` Typer group with bound methods (no wrappers)."""
    cli = MusicCli(formatter)
    app = typer.Typer(
        help="Play and manage saved audio albums (consume-only).",
        no_args_is_help=True,
    )
    app.command("list")(cli.list_programs)
    app.command("play")(cli.play)
    app.command("next")(cli.advance)
    app.command("status")(cli.status)
    return app
