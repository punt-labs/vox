"""Audio file migration from legacy ~/vox-output to ~/Music/vox."""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Self

import typer

__all__ = ["AudioMigration"]


class AudioMigration:
    """Scan, preview, and execute migration of saved audio files."""

    __slots__ = (
        "_conflicts",
        "_dst_dir",
        "_pairs",
        "_skipped",
        "_src_dir",
        "_total_size",
    )

    _src_dir: Path
    _dst_dir: Path
    _pairs: list[tuple[Path, Path]]
    _conflicts: list[tuple[Path, Path]]
    _skipped: list[tuple[Path, str]]
    _total_size: int

    def __new__(cls, src_dir: Path, dst_dir: Path) -> Self:
        self = super().__new__(cls)
        self._src_dir = src_dir
        self._dst_dir = dst_dir
        self._pairs = []
        self._conflicts = []
        self._skipped = []
        self._total_size = 0
        return self

    def scan(self) -> bool:
        """Build the move plan. Return True if there is work to do."""
        if not self._src_dir.exists():
            typer.echo("Nothing to migrate: source directory does not exist.")
            return False

        if not self._src_dir.is_dir():
            typer.echo(f"Source is not a directory: {self._src_dir}")
            raise typer.Exit(code=1)

        for src_file in sorted(self._src_dir.rglob("*")):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(self._src_dir)
            # Special case: music/ -> tracks/
            parts = rel.parts
            if parts and parts[0] == "music":
                rel = Path("tracks", *parts[1:])
            dst_file = self._dst_dir / rel
            try:
                self._total_size += src_file.stat().st_size
            except OSError as exc:
                self._skipped.append((src_file, f"unreadable ({exc})"))
                continue

            if dst_file.exists():
                self._classify_existing(src_file, dst_file)
            else:
                self._pairs.append((src_file, dst_file))

        if not self._pairs and not self._conflicts and not self._skipped:
            typer.echo("Nothing to migrate: source directory is empty.")
            return False
        return True

    def preview(self) -> None:
        """Print dry-run summary without moving files."""
        file_count = len(self._pairs) + len(self._conflicts) + len(self._skipped)
        size_mb = self._total_size / (1024 * 1024)

        typer.echo("vox migrate-audio: dry run (pass --execute to move files)\n")
        typer.echo(
            f"Source:      {self._src_dir} ({file_count} files, {size_mb:.1f} MB)"
        )
        typer.echo(f"Destination: {self._dst_dir}\n")
        for src_file, dst_file in self._pairs:
            typer.echo(f"  {src_file} -> {dst_file}")
        for src_file, dst_file in self._conflicts:
            typer.echo(f"  {src_file} -> {dst_file} [CONFLICT]")
        for src_file, reason in self._skipped:
            typer.echo(f"  {src_file} [SKIP: {reason}]")
        count = len(self._pairs)
        typer.echo(f"\n{count} files would be moved. Run with --execute.")
        if self._conflicts:
            typer.echo(f"{len(self._conflicts)} conflict(s) would be skipped.")

    def execute(self) -> None:
        """Move files and clean up empty source directories."""
        moved = 0
        moved_bytes = 0
        for src_file, dst_file in self._pairs:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src_file), str(dst_file))
                moved += 1
                moved_bytes += dst_file.stat().st_size
            except PermissionError:
                typer.echo(f"  warning: permission denied: {src_file}", err=True)
            except OSError as exc:
                typer.echo(f"  warning: {exc}", err=True)

        removed_source = self._cleanup_empty_dirs()

        moved_mb = moved_bytes / (1024 * 1024)
        typer.echo(
            f"Moved {moved} files from {self._src_dir} "
            f"to {self._dst_dir} ({moved_mb:.1f} MB)"
        )
        if self._conflicts:
            typer.echo(f"Skipped {len(self._conflicts)} conflict(s).")
        if removed_source:
            typer.echo(f"Removed empty {self._src_dir} directory.")

    # -- private helpers -----------------------------------------------------

    def _classify_existing(self, src_file: Path, dst_file: Path) -> None:
        """Classify a destination file as duplicate or conflict."""
        try:
            src_stat = src_file.stat()
            dst_stat = dst_file.stat()
        except OSError as exc:
            self._skipped.append((src_file, f"unreadable ({exc})"))
            return
        same_size = src_stat.st_size == dst_stat.st_size
        same_mtime = abs(src_stat.st_mtime - dst_stat.st_mtime) < 1.0
        if same_size and same_mtime:
            self._skipped.append((src_file, "already migrated"))
        else:
            self._conflicts.append((src_file, dst_file))

    def _cleanup_empty_dirs(self) -> bool:
        """Remove empty dirs under the source tree. Return True if root removed."""
        for dirpath in sorted(self._src_dir.rglob("*"), reverse=True):
            if dirpath.is_dir():
                with contextlib.suppress(OSError):
                    dirpath.rmdir()
        try:
            self._src_dir.rmdir()
        except OSError:
            return False
        return True
