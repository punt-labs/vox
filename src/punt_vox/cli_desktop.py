"""The ``vox desktop`` CLI verbs -- install and uninstall the MCP registration.

Nested subcommand group matching ``vox daemon`` (single-verb subcommand names,
cli.md §Subcommand naming).
:class:`DesktopCli` is a humble object: each verb parses ``--json`` /
``--verbose`` / ``--quiet``, resolves the Claude Desktop config path via
:meth:`DesktopInstaller.config_path`, drives :class:`DesktopInstaller` for the
non-secret ``env`` map, and formats via the shared :class:`OutputFormatter`.
``install`` and ``uninstall`` are symmetric so nothing the CLI can register
requires manual editing of ``claude_desktop_config.json`` to undo.

Which platforms can be registered is not a question this module answers. It
asks :meth:`DesktopInstaller.config_path` for a path and reports the refusal
it gets back; the installer owns the per-platform location, so the CLI's
verdict and doctor's read-back cannot disagree about where the file lives.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Self, final

import typer

from punt_vox.desktop_config import DesktopConfig
from punt_vox.desktop_install import DesktopInstaller
from punt_vox.dirs import default_output_dir

if TYPE_CHECKING:
    from punt_vox.cli_io import OutputFlags
    from punt_vox.output_formatter import OutputFormatter

__all__ = ["DesktopCli", "build_desktop_app"]

_JsonOutput = Annotated[bool, typer.Option("--json", help="Output JSON.")]
_Verbose = Annotated[
    bool, typer.Option("--verbose", "-v", help="Enable debug logging.")
]
_Quiet = Annotated[
    bool, typer.Option("--quiet", "-q", help="Suppress non-JSON output.")
]
_OutputDirOpt = Annotated[
    Path | None,
    typer.Option("--output-dir", "-d", help="Output directory. Default: ~/Music/vox."),
]
_UvxPathOpt = Annotated[
    str | None,
    typer.Option("--uvx-path", help="Path to uvx binary. Default: auto-detect."),
]
_ProviderOpt = Annotated[
    str | None,
    typer.Option(
        "--provider", help="TTS provider to suggest, not set. Default: auto-detect."
    ),
]


@final
class DesktopCli:
    """The ``vox desktop`` verbs bound onto a nested typer group."""

    __slots__ = ("_flags", "_formatter")

    _formatter: OutputFormatter
    _flags: OutputFlags

    # Keep newly created directories holding MCP config owner-only.
    _CONFIG_DIR_MODE: ClassVar[int] = 0o700
    # The audio directory holds no secrets: ``Path.mkdir``'s own default, which
    # the caller's umask narrows the way it does for any directory they make.
    _OUTPUT_DIR_MODE: ClassVar[int] = 0o777

    def __new__(cls, formatter: OutputFormatter, flags: OutputFlags) -> Self:
        """Create a desktop cli."""
        self = super().__new__(cls)
        self._formatter = formatter
        self._flags = flags
        return self

    def _resolve_config_path(self) -> Path:
        """Resolve this host's Claude Desktop config path, or refuse."""
        try:
            return DesktopInstaller.config_path()
        except ValueError as exc:
            self._formatter.error(str(exc), f"Error: {exc}")
            raise typer.Exit(code=1) from exc

    def _resolve_uvx(self, uvx_path: str | None) -> str:
        """Return the ``uvx`` binary Claude Desktop will launch, or refuse."""
        uvx = uvx_path or shutil.which("uvx")
        if not uvx:
            detail = "uvx not found. Install uv (https://docs.astral.sh/uv/) first."
            self._formatter.error(detail, f"Error: {detail}")
            raise typer.Exit(code=1)
        return uvx

    def _resolve_installer(
        self, provider: str | None, audio_dir: Path
    ) -> DesktopInstaller:
        """Build the installer for *provider*, or refuse when none is ready.

        ``detect(None, ...)`` raises when no provider credentials are in view
        of the installer: a fresh install with nothing ready has no provider
        to suggest.
        """
        try:
            return DesktopInstaller.detect(provider, audio_dir)
        except ValueError as exc:
            self._formatter.error(str(exc), f"Error: {exc}")
            raise typer.Exit(code=1) from exc

    def _make_dir(self, path: Path, mode: int) -> None:
        """Create *path* and its parents at *mode*, or refuse with a clean error.

        A directory the CLI cannot create -- no write permission on the parent,
        a regular file already sitting at the path, a full disk -- is the same
        refusal as an unreadable config and reports the same way: one error
        line through :class:`OutputFormatter`. An unguarded ``mkdir`` raised
        past the formatter, so a ``--json`` caller got a traceback and no
        envelope at all.
        """
        try:
            path.mkdir(parents=True, exist_ok=True, mode=mode)
        except OSError as exc:
            detail = f"Could not create {path}: {exc}"
            self._formatter.error(detail, f"Error: {detail}")
            raise typer.Exit(code=1) from exc

    def _report_nothing_to_do(self, reason: str, message: str) -> None:
        """Report an idempotent no-op: nothing was unregistered, and that is fine.

        ``uninstall`` declines two ways -- no config at all, or a config with no
        vox entry -- and neither is a failure, so both leave ``unregistered``
        false and exit 0 rather than taking the error path.
        """
        self._formatter.emit({"unregistered": False, "reason": reason}, message)

    def install(
        self,
        output_dir: _OutputDirOpt = None,
        uvx_path: _UvxPathOpt = None,
        install_provider: _ProviderOpt = None,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Register the MCP server with Claude Desktop.

        Writes the MCP registration and output directory to
        ``claude_desktop_config.json``. Desktop launches Vox in its own
        workspace so provider selection persists apart from other repos.
        The daemon reads provider keys from ``keys.env`` at startup.

        Every prerequisite is resolved before anything is created on disk, so
        a host vox cannot register with leaves no output directory behind.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)

        # Every refusal comes first: each _resolve_* raises Exit(1), so the
        # mkdirs below run only once nothing can decline. _resolve_installer
        # merely records audio_dir; it does not need the directory to exist.
        uvx = self._resolve_uvx(uvx_path)
        config_path = self._resolve_config_path()
        audio_dir = output_dir or default_output_dir()
        installer = self._resolve_installer(install_provider, audio_dir)

        self._make_dir(audio_dir, self._OUTPUT_DIR_MODE)
        self._make_dir(config_path.parent, self._CONFIG_DIR_MODE)
        config = DesktopConfig(config_path, self._formatter)
        data = config.load()
        servers = config.server_map(data)
        overwriting = "vox" in servers
        workspace = config.create_workspace()
        servers["vox"] = {
            "command": uvx,
            "args": [
                "--directory",
                str(workspace),
                "--from",
                "punt-vox",
                "vox",
                "mcp",
            ],
            "env": {**installer.server_env()},
        }
        config.write(data)

        next_step = (
            "The installer did not set a provider. "
            "If the Vox daemon is not installed, run 'vox daemon install'; "
            "if it is stopped, run 'vox daemon start'. Restart Claude Desktop. "
            "If you want to set or change your provider, ask Claude to run "
            f'the Vox provider tool with name="{installer.provider}".'
        )
        payload: dict[str, object] = {
            "registered": True,
            "overwritten": overwriting,
            "suggested_provider": installer.provider,
            "next_step": next_step,
            "config": str(config_path),
            "output": str(audio_dir),
        }
        lines = [
            "Updated existing vox entry."
            if overwriting
            else "Registered vox MCP server.",
            f"Config: {config_path}",
            f"Output: {audio_dir}",
            next_step,
        ]
        self._formatter.emit(payload, "\n".join(lines))
        if not installer.daemon_can_authenticate():
            typer.echo(installer.credential_guidance(), err=True)

    def uninstall(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Remove the vox MCP server entry from the Claude Desktop config.

        Symmetric with ``vox desktop install``. The daemon and its
        ``keys.env`` are separate concerns owned by ``vox daemon
        uninstall``; this touches only ``claude_desktop_config.json``.
        Idempotent: absent config or absent ``vox`` entry both report
        "not registered" and exit 0.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)

        config_path = self._resolve_config_path()
        if not config_path.exists():
            self._report_nothing_to_do(
                "config_absent",
                f"Claude Desktop config not found at {config_path}; nothing to do.",
            )
            return

        config = DesktopConfig(config_path, self._formatter)
        data = config.load()
        servers = data.get("mcpServers")
        if not isinstance(servers, dict) or "vox" not in servers:
            self._report_nothing_to_do(
                "vox_absent", "vox MCP server not registered; nothing to do."
            )
            return

        del servers["vox"]
        config.write(data)
        self._formatter.emit(
            {"unregistered": True, "config": str(config_path)},
            f"Removed vox MCP server from {config_path}.\n"
            "Restart Claude Desktop to apply.",
        )


def build_desktop_app(formatter: OutputFormatter, flags: OutputFlags) -> typer.Typer:
    """Return the ``vox desktop`` Typer group with bound verbs (no wrappers)."""
    cli = DesktopCli(formatter, flags)
    app = typer.Typer(
        help="Manage the Claude Desktop MCP registration.",
        no_args_is_help=True,
    )
    app.command("install")(cli.install)
    app.command("uninstall")(cli.uninstall)
    return app
