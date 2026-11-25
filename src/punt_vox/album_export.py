"""The ``music get`` export: the daemon's manifest and where the album landed.

:class:`ExportManifest` is the parsed ``music_manifest`` reply -- the album's
on-disk name, the parts the daemon can serve, and the ready parts it reported
missing. :class:`AlbumExport` is the result a client hands back.

A ready part whose file was deleted from the daemon's album directory out of band
cannot be copied, but it must not sink the parts that survive. ``AlbumExport``
carries the written directory together with the names of the parts the daemon
reported missing, so both surfaces (``vox music get`` and ``mic:music get``)
report the same fields and tell the user exactly which tracks were skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

from punt_vox.bare_name import BareName
from punt_vox.types_programs.wire import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["AlbumExport", "ExportManifest"]


@final
@dataclass(frozen=True, slots=True)
class ExportManifest:
    """A parsed ``music_manifest`` reply: album name, served parts, missing parts."""

    album: str
    parts: tuple[str, ...]
    missing: tuple[str, ...]

    @classmethod
    def parse(cls, reply: object) -> Self:
        """Return the manifest in *reply*, every name validated as a bare filename.

        Validating daemon-supplied names before any join means a tampered frame
        cannot write outside the destination.

        Raises:
            ValueError: If the reply is malformed or a name is not a bare filename.
        """
        obj = JsonObject.coerce(reply, "music_manifest")
        return cls(
            BareName(obj.require_str("album"), "album name").value,
            cls._names(obj.require_list("parts"), "manifest.parts"),
            cls._names(obj.require_list("missing"), "manifest.missing"),
        )

    @staticmethod
    def _names(items: tuple[object, ...], where: str) -> tuple[str, ...]:
        """Return the bare ``part`` name of each manifest entry in *items*."""
        return tuple(
            BareName(
                JsonObject.coerce(item, where).require_str("part"), "part name"
            ).value
            for item in items
        )

    def require_parts(self, album_id: str) -> None:
        """Refuse a manifest with nothing to export, naming why.

        The manifest lists only ready parts still on disk, so an empty list means
        every ready part was deleted (named) or the album is still generating.

        Raises:
            ValueError: If the manifest has no parts to fetch.
        """
        if self.parts:
            return
        if self.missing:
            msg = (
                f"album {album_id} has no parts to export: every ready part is "
                f"missing on disk: {', '.join(self.missing)}"
            )
            raise ValueError(msg)
        msg = f"album {album_id} has no ready parts to export (still generating)"
        raise ValueError(msg)


@final
@dataclass(frozen=True, slots=True)
class AlbumExport:
    """An exported album directory and the ready parts missing from the daemon."""

    path: Path
    missing: tuple[str, ...] = ()

    def payload(self, album_id: str) -> dict[str, object]:
        """Return the surface result: the id, the path, and any skipped parts.

        ``missing`` appears only when a part was skipped, so a complete export
        keeps the established ``{album_id, path}`` shape.
        """
        result: dict[str, object] = {"album_id": album_id, "path": str(self.path)}
        if self.missing:
            result["missing"] = list(self.missing)
        return result

    def summary(self) -> str:
        """Return the human line: the path, then a note naming any skipped part."""
        if not self.missing:
            return str(self.path)
        return f"{self.path}\nskipped (missing on disk): {', '.join(self.missing)}"
