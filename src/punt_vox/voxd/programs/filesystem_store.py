"""Filesystem implementations of the album persistence protocols.

These are the *only* modules that touch the disk for album data: all ``pathlib``
access, directory globbing, and atomic manifest writes live here. Each album is a
``<slug>-<id>`` directory under a shared root, holding a ``manifest.json`` (UTF-8)
and its ``NNN.mp3`` Part files. Manifest writes are atomic and fsynced (temp file
+ ``os.replace``) so a crash mid-write never leaves a torn manifest. This is the
seam that dereferences an opaque locator to a ``Path`` (finding #3).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Self, final

from punt_vox.voxd.programs.catalog import Album
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import AlbumManifest, ManifestDraft, PartEntry
from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.wire import JsonObject

__all__ = ["FilesystemPartStore", "FilesystemProgramStore"]

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"


@final
class FilesystemPartStore:
    """One album directory: its ``manifest.json`` and ``NNN.mp3`` Part files."""

    __slots__ = ("_directory", "_manifest")
    _directory: Path
    _manifest: AlbumManifest

    def __new__(cls, directory: Path, manifest: AlbumManifest) -> Self:
        self = super().__new__(cls)
        self._directory = directory
        self._manifest = manifest
        return self

    @property
    def directory(self) -> Path:
        """Return the album directory this store backs."""
        return self._directory

    def ready_parts(self) -> tuple[Part, ...]:
        """Return the ready Parts, ordered by intrinsic index."""
        return self._manifest.ready_parts()

    def next_index(self) -> int:
        """Return the 1-based index the next recorded Part will take."""
        return self._manifest.next_index()

    def write_target(self, index: int) -> Path:
        """Return the ``NNN.mp3`` audio path for a new Part at ``index``."""
        return self._directory / f"{index:03d}.mp3"

    def manifest(self) -> AlbumManifest:
        """Return the current manifest."""
        return self._manifest

    def prepare(self) -> None:
        """Ensure the album directory exists before a write."""
        self._directory.mkdir(parents=True, exist_ok=True)

    def record(self, entry: PartEntry) -> None:
        """Append ``entry`` to the manifest and persist it durably."""
        self._manifest = self._manifest.with_part(entry)
        self.save_manifest()

    def save_manifest(self) -> None:
        """Write the current manifest atomically (temp file + fsync + replace)."""
        self.prepare()
        tmp = self._directory / f".{_MANIFEST_NAME}.tmp"
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(self._manifest.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(self._directory / _MANIFEST_NAME)


@final
class FilesystemProgramStore:
    """The set of albums under one root directory (``~/Music/vox``)."""

    __slots__ = ("_root",)
    _root: Path

    def __new__(cls, root: Path) -> Self:
        self = super().__new__(cls)
        self._root = root
        return self

    @property
    def root(self) -> Path:
        """Return the programs root directory."""
        return self._root

    def scan(self) -> tuple[Album, ...]:
        """Return every saved album, skipping idless legacy, escaping, and corrupt.

        The one startup disk walk: for each ``*/manifest.json`` it resolves the
        directory and confirms it is contained under the root (rejecting symlinks
        and escapes, F#1), peeks ``opt_str("id")`` to skip idless legacy dirs
        (no-migration, debug-logged), then pairs the parsed manifest with its
        directory name as an :class:`Album`. Each album is scanned in isolation
        (F4): one corrupt id-bearing manifest is logged at ERROR and skipped, so a
        single torn file can never brick the catalog -- nor the whole daemon.
        """
        if not self._root.is_dir():
            return ()
        albums = [
            album
            for path in sorted(self._root.glob(f"*/{_MANIFEST_NAME}"))
            if (album := self._scan_one(path)) is not None
        ]
        return tuple(albums)

    def open(self, directory: str) -> FilesystemPartStore:
        """Return the PartStore for a scan/create-validated directory, else raise."""
        path = self._contained_dir(directory)
        manifest_path = path / _MANIFEST_NAME
        if not manifest_path.is_file():
            msg = f"no saved album at directory {directory!r}"
            raise LookupError(msg)
        manifest = AlbumManifest.from_json(manifest_path.read_text(encoding="utf-8"))
        return FilesystemPartStore(path, manifest)

    def create(self, draft: ManifestDraft) -> FilesystemPartStore:
        """Materialise ``draft`` into a fresh ``<slug>-<id>`` directory (finding #6).

        The directory name is a single validated segment (``ProgramName``), so it
        cannot traverse; ``mkdir(exist_ok=False)`` is the second-line race guard
        behind ``AlbumId.mint`` (finding #8). The store stamps ``created``.
        """
        segment = ProgramName(draft.locator)
        directory = self._contained_dir(segment.value)
        directory.mkdir(parents=True, exist_ok=False)
        store = FilesystemPartStore(directory, draft.stamped(datetime.now(UTC)))
        store.save_manifest()
        return store

    def _scan_one(self, manifest_path: Path) -> Album | None:
        """Return the Album for one manifest, or ``None`` to skip it.

        ``None`` covers three skips with two log levels. An escaping or idless
        legacy dir is an *intentional* skip (debug-logged). A corrupt id-bearing
        manifest is a real *fault* (F4): the parse raises, and rather than let it
        propagate out of ``scan`` and brick the whole daemon, it is logged at
        ERROR with the offending directory and that one album is dropped -- the
        rest of the catalog survives.
        """
        directory = manifest_path.parent
        if not self._is_contained(directory):
            logger.debug("skipping album dir outside root: %s", directory)
            return None
        try:
            text = manifest_path.read_text(encoding="utf-8")
            obj = JsonObject.parse(text, "manifest")
            if obj.opt_str("id") is None:
                logger.debug("skipping idless legacy album dir: %s", directory)
                return None
            return Album(AlbumManifest.from_wire(obj), directory.name, self)
        except (ValueError, OSError) as exc:
            logger.error(
                "skipping corrupt album manifest in %s: %s", directory.name, exc
            )
            return None

    def _contained_dir(self, directory: str) -> Path:
        """Return ``root/directory``, raising if it escapes the programs root."""
        path = self._root / directory
        if not self._is_contained(path):
            msg = f"album directory escapes the programs root: {directory!r}"
            raise ValueError(msg)
        return path

    def _is_contained(self, directory: Path) -> bool:
        """Return whether ``directory`` resolves to a path under the root."""
        root = self._root.resolve()
        resolved = directory.resolve()
        return resolved == root or root in resolved.parents
