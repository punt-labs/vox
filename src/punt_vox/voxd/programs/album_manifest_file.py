"""Read a saved album manifest without following links or unbounded reads."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.store_errors import AlbumNotFoundError

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["AlbumManifestFile"]

# A manifest is small JSON (album tags + up to a pool of Part entries); anything
# past this ceiling is corrupt or a planted file, and is rejected before the read
# so one giant manifest cannot exhaust daemon memory at scan time.
_MAX_MANIFEST_BYTES = 1024 * 1024


@final
class AlbumManifestFile:
    """Read the on-disk UTF-8 manifest for one album."""

    __slots__ = ("_path",)
    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Bind the manifest path."""
        self = super().__new__(cls)
        self._path = path
        return self

    def read_saved(self) -> str:
        """Require a regular manifest in a previously catalogued album.

        Raises:
            AlbumNotFoundError: If the manifest is missing or disappears during open.
            OSError: If the manifest cannot be inspected or read safely.
        """
        try:
            self._require_regular_file()
            return self.read_text()
        except FileNotFoundError as exc:
            msg = f"no saved album at directory {self._path.parent.name!r}"
            raise AlbumNotFoundError(msg) from exc

    def _require_regular_file(self) -> None:
        """Reject nonregular manifest entries without opening them."""
        mode = self._path.stat(follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            msg = f"manifest is not a regular file: {self._path}"
            raise OSError(msg)

    def read_text(self) -> str:
        """Return UTF-8 text, rejecting symlinks, oversized files and bad UTF-8.

        Bound the read itself: the file can grow after open, so checking its
        reported size alone would not limit memory use. Read-integrity failures
        raise ``OSError`` so they audit as server faults, not client rejections.
        """
        raw = self._read_bytes()
        if len(raw) > _MAX_MANIFEST_BYTES:
            msg = f"manifest exceeds {_MAX_MANIFEST_BYTES} bytes: {self._path.name}"
            raise OSError(msg)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"manifest is not valid UTF-8: {self._path.name}"
            raise OSError(msg) from exc

    def _read_bytes(self) -> bytes:
        """Adopt the raw descriptor or close it if adoption fails."""
        fd = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            handle = os.fdopen(fd, "rb")
        except OSError:
            os.close(fd)
            raise
        with handle:
            return handle.read(_MAX_MANIFEST_BYTES + 1)
