"""The ``music get`` manifest value objects: one album's name and its ready parts.

:class:`AlbumContents` is what a ``music get`` reply carries -- the album's on-disk
name, a :class:`PartFile` per ready part still on disk, and the names of ready
parts whose files were deleted out of band. Each ``PartFile`` measures itself
from the store: its bare name comes from the on-disk manifest, so it is
bare-name-validated inside the album directory (:meth:`ContainmentRoot.contained_child`)
*before* it is stat'd -- a corrupt or hostile manifest entry bearing a separator
or ``..`` can never stat a file outside the album directory.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.containment import ContainmentRoot
    from punt_vox.voxd.programs.catalog import Album
    from punt_vox.voxd.programs.part import Part

__all__ = ["AlbumContents", "PartFile"]


@final
@dataclass(frozen=True, slots=True)
class PartFile:
    """One album part on disk: its bare file name and byte count (the get manifest)."""

    name: str
    byte_count: int

    @classmethod
    def measured(cls, root: ContainmentRoot, part: Part) -> Self:
        """Return ``part`` sized from disk, validating its name within ``root``.

        Sizes the part with ``lstat`` (no symlink follow) and requires a regular
        file: a manifest entry that resolves to a symlink or directory is corrupt
        and raises ``ValueError`` (the manifest handler turns it into a clean wire
        error) rather than leaking the byte count of whatever the link targets.

        Raises:
            ValueError: If the audio path is not a regular file.
        """
        contained = root.contained_child(part.identity)
        info = contained.lstat()
        if not stat.S_ISREG(info.st_mode):
            msg = f"part {part.identity!r} is not a regular file"
            raise ValueError(msg)
        return cls(part.identity, info.st_size)


@final
@dataclass(frozen=True, slots=True)
class AlbumContents:
    """An album's on-disk name, its ready parts, and the ready parts gone from disk.

    ``missing`` names ready parts whose files were deleted out of band, so a
    ``music get`` still exports every surviving part and can say which it skipped.
    """

    name: str
    parts: tuple[PartFile, ...]
    missing: tuple[str, ...] = ()

    @classmethod
    def from_album(cls, album: Album, root: ContainmentRoot) -> Self:
        """Return the get manifest: the album name, sized parts, and missing parts.

        Only absence (``FileNotFoundError``) marks a part missing; an unsafe name,
        a non-regular file, or any other read fault still raises.

        Raises:
            ValueError: If a part name is unsafe or not a regular file.
            OSError: If a part file cannot be inspected for a reason other than absence.
        """
        parts: list[PartFile] = []
        missing: list[str] = []
        for part in album.ready_parts():
            try:
                parts.append(PartFile.measured(root, part))
            except FileNotFoundError:
                missing.append(part.identity)
        return cls(album.locator, tuple(parts), tuple(missing))

    def wire(self) -> dict[str, object]:
        """Return the ``manifest`` reply frame the ``music get`` client parses."""
        return {
            "type": "manifest",
            "album": self.name,
            "parts": [
                {"part": part.name, "bytes": part.byte_count} for part in self.parts
            ],
            "missing": [{"part": name} for name in self.missing],
        }
