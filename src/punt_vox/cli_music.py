"""The consume-only ``vox music`` CLI -- play and manage saved audio Programs.

The CLI never authors (no LLM, no generation): it lists, plays, loops, advances,
and shows status. :class:`MusicCli` is a humble object testable with an in-memory
gateway and formatter; playback crosses to ``voxd`` via a :class:`ProgramGateway`,
other reads hit the store (design §4).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn, Self, final

import typer
from websockets.exceptions import WebSocketException

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_gateway import ClientProgramGateway
from punt_vox.client_sync import VoxClientSync
from punt_vox.dirs import default_output_dir
from punt_vox.output_formatter import OutputFormatter
from punt_vox.program_gateway import ProgramGateway
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.identifiers import PartRef, ProgramName
from punt_vox.voxd.programs.status import ProgramStatus

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
    def _programs_root() -> Path:
        """Return the root directory under which saved Programs live.

        Each Program is a pool directory directly under the music root
        (``~/Music/vox/<name>/``), so macOS Music.app and Ubuntu players scan
        the pools without an intervening ``programs/`` segment to descend past.
        """
        return default_output_dir()

    @staticmethod
    def _fail(message: str) -> NoReturn:
        """Print an error to stderr and exit non-zero -- a clean CLI failure."""
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1)

    def list_programs(self) -> None:
        """List saved Programs, grouped by name with their ready/total counts."""
        programs = FilesystemProgramStore(self._programs_root()).list_programs()
        if not programs:
            self._formatter.emit({"programs": []}, "No saved programs.")
            return
        rows = [(m.name.value, len(m.ready_parts()), len(m.parts)) for m in programs]
        entries = [{"name": n, "ready": r, "total": t} for n, r, t in rows]
        listing = "\n".join(f"  {n} — {r}/{t} part(s)" for n, r, t in rows)
        payload = {"programs": entries}
        self._formatter.emit(payload, f"{len(rows)} saved program(s):\n{listing}")

    def play(
        self,
        name: Annotated[str, typer.Argument(help="Saved Program name to play.")],
        part: Annotated[
            str | None,
            typer.Argument(help="Optional part address, e.g. 'playlist:2'."),
        ] = None,
    ) -> None:
        """Play a saved Program from disk, optionally at a specific part."""
        ref = self._resolve_part(name, part)
        try:
            outcome = self._gateway_factory().play(ProgramName(name), ref)
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(
            {"music": "play", "name": name, "applied": outcome.applied},
            outcome.display(f"Playing {name}."),
        )

    def loop(
        self,
        name: Annotated[str, typer.Argument(help="Saved Program name to loop.")],
    ) -> None:
        """Play a saved Program and rotate on every track end."""
        try:
            outcome = self._gateway_factory().loop(ProgramName(name))
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(
            {"music": "loop", "name": name, "applied": outcome.applied},
            outcome.display(f"Looping {name}."),
        )

    def advance(self) -> None:
        """Advance the active Program to another Part."""
        try:
            outcome = self._gateway_factory().advance()
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(
            {"music": "next", "applied": outcome.applied},
            outcome.display("Advancing to another part."),
        )

    def status(self) -> None:
        """Show the active Program's authoritative status."""
        try:
            report = self._gateway_factory().status()
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(report.to_dict(), self._render_status(report))

    def _resolve_part(self, name: str, token: str | None) -> PartRef | None:
        """Resolve an optional ``playlist:N`` token, or ``None`` when none is given.

        A bad token/program/format/index fails cleanly before any transition (#7).
        """
        if token is None:
            return None
        store = FilesystemProgramStore(self._programs_root())
        try:
            ref = PartRef.parse(token)
            manifest = store.open(ProgramName(name)).manifest()
            if ref.format is not manifest.format:
                raise ValueError(f"{name} is not a {ref.format.value} program")
            manifest.resolve_part(ref)  # validate by intrinsic index, not position
        except (ValueError, LookupError) as exc:
            self._fail(str(exc))
        return ref

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
        help="Play and manage saved audio Programs (consume-only).",
        no_args_is_help=True,
    )
    app.command("list")(cli.list_programs)
    app.command("play")(cli.play)
    app.command("loop")(cli.loop)
    app.command("next")(cli.advance)
    app.command("status")(cli.status)
    return app
