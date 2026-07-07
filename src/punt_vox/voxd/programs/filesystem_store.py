"""Filesystem implementations of the Program/Part persistence protocols.

These are the *only* modules that touch the disk for Program data: all
``pathlib`` access, directory globbing, and atomic manifest writes live here.
Each Program is a directory under a shared root, holding a ``manifest.json``
(UTF-8) and its ``NNN.mp3`` Part files. Manifest writes are atomic and fsynced
(temp file + ``os.replace``) so a crash mid-write never leaves a torn manifest.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Self, final

from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import PartEntry, ProgramManifest
from punt_vox.voxd.programs.part import Part

__all__ = ["FilesystemPartStore", "FilesystemProgramStore"]

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"


@final
class FilesystemPartStore:
    """One Program directory: its ``manifest.json`` and ``NNN.mp3`` Part files."""

    __slots__ = ("_directory", "_manifest")
    _directory: Path
    _manifest: ProgramManifest

    def __new__(cls, directory: Path, manifest: ProgramManifest) -> Self:
        self = super().__new__(cls)
        self._directory = directory
        self._manifest = manifest
        return self

    @property
    def directory(self) -> Path:
        """Return the Program directory this store backs."""
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

    def manifest(self) -> ProgramManifest:
        """Return the current manifest."""
        return self._manifest

    def prepare(self) -> None:
        """Ensure the Program directory exists before a write."""
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
    """The set of Programs under one root directory (``~/Music/vox``)."""

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

    def list_programs(self) -> tuple[ProgramManifest, ...]:
        """Return every saved Program's manifest, ordered by name."""
        if not self._root.is_dir():
            return ()
        manifests = tuple(
            self._read(path) for path in sorted(self._root.glob(f"*/{_MANIFEST_NAME}"))
        )
        return tuple(sorted(manifests, key=lambda m: m.name.value))

    def resolve(self, name: ProgramName) -> ProgramManifest | None:
        """Return the manifest for ``name``, or ``None`` if none is saved."""
        path = self._manifest_path(name)
        if not path.is_file():
            return None
        return self._read(path)

    def open(self, name: ProgramName) -> FilesystemPartStore:
        """Return the PartStore for an existing Program, raising if absent."""
        manifest = self.resolve(name)
        if manifest is None:
            msg = f"no saved program named {name.value!r}"
            raise LookupError(msg)
        return FilesystemPartStore(self._root / name.value, manifest)

    def create(self, manifest: ProgramManifest) -> FilesystemPartStore:
        """Create a new Program from ``manifest`` and return its PartStore."""
        store = FilesystemPartStore(self._root / manifest.name.value, manifest)
        store.save_manifest()
        return store

    def _manifest_path(self, name: ProgramName) -> Path:
        return self._root / name.value / _MANIFEST_NAME

    @staticmethod
    def _read(path: Path) -> ProgramManifest:
        return ProgramManifest.from_json(path.read_text(encoding="utf-8"))
