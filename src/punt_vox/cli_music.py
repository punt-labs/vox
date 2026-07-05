"""The consume-only ``vox music`` CLI -- play and manage saved audio Programs.

The CLI never authors (no LLM, no generation): it lists saved Programs, plays or
loops one from disk, advances the active one, shows the daemon's authoritative
status, and runs the one-time legacy migration. :class:`MusicCli` owns that
behaviour as a humble object -- each method is directly testable with an
in-memory gateway and formatter, no ``CliRunner`` -- and :func:`build_music_app`
wires the methods onto a Typer group. Playback control crosses to ``voxd`` via a
:class:`ProgramGateway`; listing, part resolution, and migration read the
filesystem store directly (design section 4).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn, Self, final

import typer
from websockets.exceptions import WebSocketException

from punt_vox.client import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_gateway import ClientProgramGateway
from punt_vox.client_sync import VoxClientSync
from punt_vox.dirs import default_output_dir, music_output_dir
from punt_vox.output_formatter import OutputFormatter
from punt_vox.program_gateway import ProgramGateway
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.identifiers import PartRef, ProgramName
from punt_vox.voxd.programs.migrate import LegacyMigration, MigrationError
from punt_vox.voxd.programs.status import ProgramStatus

__all__ = ["MusicCli", "build_music_app"]

# A client error (unreachable/misbehaving daemon), a raw WebSocket failure (a
# stale token's handshake, a mid-request close), or a bad Program name (a
# ValueError from ProgramName) becomes a clean, non-zero CLI failure -- the MCP
# tools already catch WebSocketException, so the CLI must too (finding F1).
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
        """Return the root directory under which saved Programs live."""
        return default_output_dir() / "programs"

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
        payload = {
            "programs": [{"name": n, "ready": r, "total": t} for n, r, t in rows]
        }
        listing = "\n".join(f"  {n} — {r}/{t} part(s)" for n, r, t in rows)
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
            outcome.message,
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
            outcome.message,
        )

    def advance(self) -> None:
        """Advance the active Program to another Part."""
        try:
            outcome = self._gateway_factory().advance()
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(
            {"music": "next", "applied": outcome.applied}, outcome.message
        )

    def status(self) -> None:
        """Show the active Program's authoritative status."""
        try:
            report = self._gateway_factory().status()
        except _GATEWAY_ERRORS as exc:
            self._fail(str(exc))
        self._formatter.emit(report.to_dict(), self._render_status(report))

    def migrate(self) -> None:
        """Migrate the legacy flat ``tracks/`` layout into named Programs (one-time)."""
        try:
            report = LegacyMigration(music_output_dir(), self._programs_root()).run()
        except MigrationError as exc:
            self._fail(str(exc))
        self._formatter.emit(
            {"migrated": report.parts, "programs": list(report.names)},
            report.summary(),
        )

    def _resolve_part(self, name: str, token: str | None) -> PartRef | None:
        """Resolve an optional ``playlist:N`` token against the saved manifest.

        A malformed token, an unknown Program, or an out-of-range index is a
        clean CLI error reported *before* any transition -- finding #7 (no such
        transition exists). Returns ``None`` when no token is given.
        """
        if token is None:
            return None
        try:
            ref = PartRef.parse(token)
        except ValueError as exc:
            self._fail(str(exc))
        manifest = FilesystemProgramStore(self._programs_root()).resolve(
            ProgramName(name)
        )
        if manifest is None:
            self._fail(f"no saved program named {name!r}")
        if ref.format is not manifest.format:
            self._fail(
                f"{name} is a {manifest.format.label}; "
                f"cannot address it as {ref.format.value}"
            )
        ready = manifest.ready_parts()
        if not 1 <= ref.index <= len(ready):
            self._fail(f"{name} has {len(ready)} parts; {ref.index} is out of range")
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
    """Return the ``vox music`` Typer group wired to a :class:`MusicCli`.

    Registers the bound methods directly, so there are no throwaway wrapper
    functions -- Typer reads each method's own ``Annotated`` argument metadata.
    """
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
    app.command("migrate")(cli.migrate)
    return app
