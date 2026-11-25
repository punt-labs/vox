"""Present daemon cache operations on the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.client_sync import VoxClientSync
    from punt_vox.output_formatter import OutputFormatter


@final
class CacheCli:
    """Report cache results and map daemon errors to a CLI exit."""

    __slots__ = ("_formatter",)
    _formatter: OutputFormatter

    def __new__(cls, formatter: OutputFormatter) -> Self:
        """Create a cache presenter with the selected output formatter."""
        self = super().__new__(cls)
        self._formatter = formatter
        return self

    def status(self, client: VoxClientSync) -> None:
        """Emit the entry count, size, and path of the daemon cache."""
        info = self._run(client.cache_status)
        size_kb = info.size_bytes / 1024
        payload = {
            "entries": info.entries,
            "size_bytes": info.size_bytes,
            "path": str(info.path),
        }
        text = (
            f"Entries: {info.entries}\nSize:    {size_kb:.1f} KB\nPath:    {info.path}"
        )
        self._formatter.emit(payload, text)

    def clear(self, client: VoxClientSync) -> None:
        """Clear the daemon cache and emit the count of removed files."""
        count = self._run(client.cache_clear)
        self._formatter.emit({"cleared": count}, f"Cleared {count} cached files.")

    def _run[T](self, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            self._formatter.error(str(exc), f"Error: {exc}")
            raise typer.Exit(code=1) from exc
