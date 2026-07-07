"""Tests for the filesystem store and its in-memory fake parity."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from punt_vox.voxd.programs import Format, Part, ProgramName
from punt_vox.voxd.programs.filesystem_store import (
    FilesystemPartStore,
    FilesystemProgramStore,
)
from punt_vox.voxd.programs.manifest import (
    PartEntry,
    PlaylistSubject,
    ProgramManifest,
)
from punt_vox.voxd.programs.store import PartStore, ProgramStore

ManifestFactory = Callable[..., ProgramManifest]
EntryFactory = Callable[..., PartEntry]


def _bypassed_name(value: str) -> ProgramName:
    """Build a ProgramName carrying ``value`` without running its validation.

    Simulates a future bypass of the value object's guard so the store's own
    defense-in-depth check can be exercised on a traversal name the constructor
    would otherwise reject.
    """
    name = object.__new__(ProgramName)
    name._value = value  # forge a would-be-rejected identity
    return name


def _manifest_named(name: ProgramName) -> ProgramManifest:
    """Build a minimal playlist manifest carrying ``name``."""
    return ProgramManifest(
        name=name,
        fmt=Format.PLAYLIST,
        subject=PlaylistSubject(vibe="ambient", style="techno"),
        parts=(),
    )


class TestFilesystemProgramStore:
    def test_create_then_resolve_round_trips(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        store = FilesystemProgramStore(tmp_path)
        manifest = manifest_of("ambient_techno", 1, 2)
        store.create(manifest)
        assert store.resolve(ProgramName("ambient_techno")) == manifest

    def test_resolve_absent_returns_none(self, tmp_path: Path) -> None:
        store = FilesystemProgramStore(tmp_path)
        assert store.resolve(ProgramName("nope")) is None

    def test_list_programs_sorted(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        store = FilesystemProgramStore(tmp_path)
        store.create(manifest_of("zed", 1))
        store.create(manifest_of("alpha", 1))
        names = [m.name.value for m in store.list_programs()]
        assert names == ["alpha", "zed"]

    def test_list_programs_empty_root(self, tmp_path: Path) -> None:
        assert FilesystemProgramStore(tmp_path / "missing").list_programs() == ()

    def test_open_absent_raises(self, tmp_path: Path) -> None:
        store = FilesystemProgramStore(tmp_path)
        with pytest.raises(LookupError, match="no saved program"):
            store.open(ProgramName("ghost"))

    def test_manifest_written_utf8(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        store = FilesystemProgramStore(tmp_path)
        store.create(manifest_of("ambient_techno", 1))
        text = (tmp_path / "ambient_techno" / "manifest.json").read_text(
            encoding="utf-8"
        )
        assert '"format": "playlist"' in text


class TestFilesystemPartStore:
    def test_record_persists_and_reopens(
        self, tmp_path: Path, manifest_of: ManifestFactory, entry_of: EntryFactory
    ) -> None:
        store = FilesystemProgramStore(tmp_path)
        part_store = store.create(manifest_of("ambient_techno"))
        part_store.record(entry_of(1))
        reopened = store.open(ProgramName("ambient_techno"))
        assert reopened.ready_parts() == (Part("001.mp3", 1),)

    def test_next_index_and_write_target(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        part_store = FilesystemProgramStore(tmp_path).create(manifest_of("x", 1, 2))
        assert part_store.next_index() == 3
        assert part_store.write_target(3) == tmp_path / "x" / "003.mp3"

    def test_prepare_creates_directory(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        part_store = FilesystemPartStore(tmp_path / "prog", manifest_of("prog"))
        part_store.prepare()
        assert (tmp_path / "prog").is_dir()

    def test_manifest_accessor(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        manifest = manifest_of("x", 1)
        part_store = FilesystemPartStore(tmp_path / "x", manifest)
        assert part_store.manifest() == manifest

    def test_directory_and_root_accessors(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        assert FilesystemProgramStore(tmp_path).root == tmp_path
        assert (
            FilesystemPartStore(tmp_path / "x", manifest_of("x")).directory
            == tmp_path / "x"
        )


class TestPathTraversalGuard:
    """The store refuses names that resolve outside the programs root."""

    def test_valid_name_still_creates(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        store = FilesystemProgramStore(tmp_path / "root")
        store.create(manifest_of("trance", 1))
        assert store.resolve(ProgramName("trance")) is not None

    @pytest.mark.parametrize("escape", ["..", "../../etc"])
    def test_create_rejects_traversal_name(self, tmp_path: Path, escape: str) -> None:
        root = tmp_path / "root"
        store = FilesystemProgramStore(root)
        with pytest.raises(ValueError, match="escapes the programs root"):
            store.create(_manifest_named(_bypassed_name(escape)))
        assert not (tmp_path / "manifest.json").exists()

    def test_resolve_rejects_traversal_name(self, tmp_path: Path) -> None:
        store = FilesystemProgramStore(tmp_path / "root")
        with pytest.raises(ValueError, match="escapes the programs root"):
            store.resolve(_bypassed_name(".."))

    def test_open_rejects_traversal_name(self, tmp_path: Path) -> None:
        store = FilesystemProgramStore(tmp_path / "root")
        with pytest.raises(ValueError, match="escapes the programs root"):
            store.open(_bypassed_name(".."))


class TestProtocolConformance:
    def test_filesystem_satisfies_protocols(
        self, tmp_path: Path, manifest_of: ManifestFactory
    ) -> None:
        program_store: ProgramStore = FilesystemProgramStore(tmp_path)
        part_store: PartStore = program_store.create(manifest_of("x"))
        assert isinstance(program_store, ProgramStore)
        assert isinstance(part_store, PartStore)

    def test_in_memory_satisfies_protocols(
        self, program_store: ProgramStore, manifest_of: ManifestFactory
    ) -> None:
        part_store: PartStore = program_store.create(manifest_of("x"))
        assert isinstance(program_store, ProgramStore)
        assert isinstance(part_store, PartStore)


class TestParity:
    """The in-memory fake must behave like the filesystem store."""

    def test_create_record_resolve_parity(
        self,
        tmp_path: Path,
        program_store: ProgramStore,
        manifest_of: ManifestFactory,
        entry_of: EntryFactory,
    ) -> None:
        fs = FilesystemProgramStore(tmp_path)
        for store in (fs, program_store):
            part_store = store.create(manifest_of("ambient_techno", 1))
            part_store.record(entry_of(2))
        name = ProgramName("ambient_techno")
        fs_manifest = fs.resolve(name)
        assert fs_manifest is not None
        assert fs_manifest == program_store.resolve(name)

    def test_missing_parity(self, tmp_path: Path, program_store: ProgramStore) -> None:
        fs = FilesystemProgramStore(tmp_path)
        assert fs.resolve(ProgramName("nope")) is None
        assert program_store.resolve(ProgramName("nope")) is None
