"""Read and update the Claude Desktop MCP registration document."""

from __future__ import annotations

import json
from contextlib import suppress
from typing import TYPE_CHECKING, ClassVar, Self, cast, final

import typer

from punt_vox.atomic_file import AtomicFile
from punt_vox.dirs import DEFAULT_CONFIG_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.output_formatter import OutputFormatter

__all__ = ["DesktopConfig"]

type _JsonValue = str | int | float | bool | list[_JsonValue] | _JsonObject | None
type _JsonObject = dict[str, _JsonValue]


@final
class DesktopConfig:
    """A Claude Desktop configuration file and its CLI error boundary."""

    __slots__ = ("_formatter", "_path")

    _formatter: OutputFormatter
    _path: Path
    _CONFIG_MODE: ClassVar[int] = 0o600

    def __new__(cls, path: Path, formatter: OutputFormatter) -> Self:
        """Bind the document path and CLI formatter."""
        self = super().__new__(cls)
        self._path = path
        self._formatter = formatter
        return self

    def load(self) -> _JsonObject:
        """Read a Claude Desktop config JSON object, or ``{}`` if absent.

        A non-object top level (list/string) would crash deep inside the
        caller's ``setdefault`` merge; rejected here with a clean Typer error.
        Bytes that are not UTF-8 fail in ``read_text`` rather than in
        ``json.loads``, so ``UnicodeDecodeError`` is caught alongside the
        parse and I/O failures -- it is the same "unreadable config" verdict.
        """
        config_path = self._path
        if not config_path.exists():
            return {}
        try:
            parsed: object = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise self._error(f"Could not read {config_path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise self._error(f"{config_path} must be a JSON object.")
        return cast("_JsonObject", parsed)

    def server_map(self, data: _JsonObject) -> _JsonObject:
        """Return the config's ``mcpServers`` object, creating it when absent.

        A non-object value under that key is the case that bites silently:
        ``"vox" in servers`` against a string is a *substring* test, so a
        hand-edited ``"mcpServers": "vox"`` reads as an existing registration
        and the assignment right after it raises. Rejected here with the same
        clean Typer error a non-object top level gets.
        """
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise self._error(
                f'{self._path} must have a JSON object under "mcpServers".'
            )
        return servers

    def create_workspace(self) -> Path:
        """Anchor Desktop's Vox state before ancestor config discovery can run."""
        workspace = self._path.parent / "vox"
        marker = workspace / DEFAULT_CONFIG_DIR / "vox.md"
        try:
            marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with suppress(FileExistsError):
                marker.touch(mode=self._CONFIG_MODE, exist_ok=False)
        except OSError as exc:
            raise self._error(f"Could not initialize Desktop Vox state: {exc}") from exc
        return workspace

    def write(self, data: _JsonObject) -> None:
        """Replace the config atomically, or refuse with a clean error.

        ``claude_desktop_config.json`` is not vox's file: every other MCP
        server keeps its own ``env`` block there, secrets included, and vox
        rewrites the whole document to change one key of it. A
        truncate-then-write that dies mid-stream would destroy *their* entries,
        so the rewrite goes through :class:`AtomicFile` -- rename over a
        fully-written, ``fsync``-ed sibling temp, written *through* a symlinked
        config rather than clobbering the link. ``_CONFIG_MODE`` is forced so
        an existing world-readable config is narrowed to owner-only, matching
        what the Claude Desktop app itself writes.

        Symmetric with :meth:`load`: a filesystem failure at this
        boundary becomes one Typer error line, not a traceback. Text that
        cannot be encoded to UTF-8 fails in the write rather than in
        ``json.dumps``, so ``UnicodeError`` is caught alongside the I/O
        failures -- it is the same "unwritable config" verdict.
        """
        config_path = self._path
        try:
            AtomicFile(config_path).replace(
                json.dumps(data, indent=2) + "\n", mode=self._CONFIG_MODE
            )
        except (OSError, UnicodeError) as exc:
            raise self._error(f"Could not write {config_path}: {exc}") from exc

    def _error(self, detail: str) -> typer.Exit:
        self._formatter.error(detail, f"Error: {detail}")
        return typer.Exit(code=1)
