"""Daemon-owned recording store: the one place a record write can land.

A wire client never names a daemon path. It supplies at most a **bare name**
(or nothing, in which case the store content-addresses by text); the store
rejects anything absolute, separated, traversing, empty, or NUL-bearing, then
resolves the candidate under a daemon-owned root and **verifies containment**
before writing. This is the  (P1) security primitive: the token
authorizes audio operations, not filesystem writes as the daemon user, so no
request -- local or remote -- can escape the root.

The write itself is atomic: the owned source is moved with an atomic rename.
On cross-filesystem ``EXDEV``, it is copied through the private descriptor
``mkstemp`` returned, then atomically renamed onto the destination.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from itertools import batched
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.types import generate_filename
from punt_vox.voxd.containment import ContainmentRoot
from punt_vox.voxd.data_root_boundary import relativize_to_data_root
from punt_vox.voxd.path_status import PathStatus

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["RecordStore", "RecordWrite"]

_NAME_LABEL = "recording name"
_SCAN_BATCH_SIZE = 128


@dataclass(frozen=True, slots=True)
class RecordWrite:
    """The landed recording: its final path and byte count.

    ``byte_count`` is the size the daemon wrote, echoed to the client so the
    caller can assert the on-disk file matches (byte-correct delivery).
    """

    path: Path
    byte_count: int

    @property
    def wire_path(self) -> str:
        """Return the landed path relativized for the wire, never a host prefix."""
        rel = relativize_to_data_root(self.path)
        return str(rel.path) if rel is not None else self.path.name


@final
@dataclass(frozen=True, slots=True)
class RecordingEntry:
    """One recording in the store: its bare name and byte count (the list view)."""

    name: str
    byte_count: int


@final
class RecordStore:
    """Own the recordings root and every path decision within it.

    All naming, containment, and the atomic write live here so the containment
    invariant is enforced in exactly one place and is unit-testable without a
    socket. ``resolve`` and ``resolve_ref`` share one validator, so record
    naming and play/fetch references reject the same hostile inputs.
    """

    __slots__ = ("_root",)

    _root: Path

    def __new__(cls, root: Path) -> Self:
        """Create a record store."""
        self = super().__new__(cls)
        self._root = root
        return self

    @property
    def root(self) -> Path:
        """Return the recordings root every write is contained within."""
        return self._root

    def resolve(self, name: str | None, text: str) -> Path:
        """Resolve the destination for a record write, contained in the root.

        A client-supplied *name* is validated as a bare filename; only ``None``
        (absent) content-addresses by *text* -- the canonical name every other
        vox MP3 uses. An explicit empty string is an invalid name, not "absent",
        so it is rejected (``ValueError``), as are absolute, separated,
        traversing, and NUL-bearing names.
        """
        candidate = generate_filename(text) if name is None else name
        return self._resolve_within_root(candidate)

    def resolve_ref(self, ref: str) -> Path:
        """Resolve a play/fetch reference to a contained store path.

        Same validation as :meth:`resolve`: a bare name only, no path escape.
        The caller checks existence -- an unknown but well-formed name resolves
        to a path inside the root that simply does not exist yet.
        """
        return self._resolve_within_root(ref)

    def place(self, *, source: Path, text: str, name: str | None) -> RecordWrite:
        """Land *source* at its contained destination atomically; return path + bytes.

        The owned source is moved with an atomic rename, falling back to a
        copy only on cross-filesystem ``EXDEV``. Either way the destination
        is replaced atomically, never with a partial write.
        """
        dest = self.resolve(name, text)
        # dest is a bare name under the root, so the parent is the root itself;
        # create it 0700 defensively (the daemon also does this at startup).
        self._root.mkdir(parents=True, exist_ok=True)
        self._root.chmod(0o700)

        moved = self._move(source, dest)
        return moved if moved is not None else self._copy(source, dest)

    def entry_batches(self) -> Iterator[list[RecordingEntry]]:
        """Yield bounded batches of immediate, non-symlink recording files.

        A missing root is empty. An inaccessible root raises OSError.
        """
        if not PathStatus.of(self._root).is_directory:
            return
        # Build the shared bare-name validator once; every child is judged by the
        # same rules ``resolve_ref``/``remove`` apply, so list and operate agree.
        validator = ContainmentRoot(self._root, _NAME_LABEL)
        with os.scandir(self._root) as children:
            for batch in batched(children, _SCAN_BATCH_SIZE, strict=False):
                yield [
                    entry
                    for child in batch
                    if (entry := self._entry_for(Path(child.path), validator))
                    is not None
                ]

    @staticmethod
    def _entry_for(child: Path, validator: ContainmentRoot) -> RecordingEntry | None:
        """Return *child*'s entry, or ``None`` to skip an unlistable child.

        One ``lstat`` (no symlink follow) both classifies and sizes the child, so
        a symlink or directory is excluded and the name+size come from the same
        syscall -- no second stat to race. A child unlinked mid-scan (a TOCTOU
        race) raises ``OSError`` here and is skipped, so a listing is best-effort
        and never fails on a concurrent delete.

        A name *validator* would refuse (a backslash-, separator-, or
        non-printable-bearing planted file) is skipped too, so the scan surfaces
        only names ``resolve_ref``/``remove`` would accept -- list and operate stay
        consistent, never showing an id that ``rec get``/``rec remove`` then reject.
        ``None`` means "skip this entry", not a give-up on producing a value
        (PY-EH-8).
        """
        try:
            info = child.lstat()
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        try:
            validator.contained_child(child.name)
        except ValueError:
            return None
        return RecordingEntry(child.name, info.st_size)

    def remove(self, ref: str) -> None:
        """Delete one in-root recording by its bare name, or raise.

        ``ref`` runs through the shared validator (a hostile name raises
        ``ValueError`` before any filesystem touch). It is then acted on *in
        place* -- ``root / ref`` without a symlink-following ``.resolve()`` -- so
        removing a symlink entry unlinks the link, never the file it points at.
        A delete can therefore never reach a recording elsewhere in the root or a
        file outside it. A well-formed but absent recording raises
        ``FileNotFoundError`` so a client can trust a success; an ``OSError`` from
        the classify or the unlink (a permission or device fault) propagates
        unchanged -- an access fault is never mislabeled a benign "not found".

        Raises:
            FileNotFoundError: If the reference is neither a stored file nor a symlink.
        """
        path = self._child_within_root(ref)
        # A no-follow classify accepts a link entry (even a broken one) so its
        # removal deletes the link, and a real recording; a directory or an absent
        # name matches neither and is "not found". Classifying from one stat means
        # a ``PermissionError`` surfaces rather than reading as a false "absent".
        status = PathStatus.of(path, follow_symlinks=False)
        if not (status.is_symlink or status.is_regular_file):
            msg = f"no recording named {ref!r}"
            raise FileNotFoundError(msg)
        path.unlink()

    def _resolve_within_root(self, candidate: str) -> Path:
        """Validate a bare name and resolve it within the shared containment root."""
        return ContainmentRoot(self._root, _NAME_LABEL).resolve(candidate)

    def _child_within_root(self, name: str) -> Path:
        """Validate a bare name and return its child under the root, unfollowed.

        The removal counterpart of :meth:`_resolve_within_root`: the shared
        validator rejects a hostile name, but the entry is *not* symlink-resolved,
        so acting on it touches the link itself rather than its target.
        """
        return ContainmentRoot(self._root, _NAME_LABEL).contained_child(name)

    @staticmethod
    def _move(source: Path, dest: Path) -> RecordWrite | None:
        """Atomically rename *source* onto *dest*, or None when it can't (EXDEV).

        Only a cross-filesystem rename (``EXDEV``) warrants the copy fallback;
        for any other ``OSError`` (``EACCES``, ``ENOENT``, ...) the copy path
        would not help and would mask the real cause, so re-raise it.

        The byte count is read from the *source* before the rename: reading
        ``dest`` afterwards would report a concurrent same-name write's size, not
        the bytes this call landed (mirrors the cross-device copy path).
        """
        try:
            byte_count = source.stat().st_size
            source.replace(dest)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                return None  # cross-device -- caller falls back to copy
            raise
        # An ephemeral source may not be private; the copy path's mkstemp temp is
        # 0600, so match that here to keep the recording private.
        dest.chmod(0o600)
        return RecordWrite(path=dest, byte_count=byte_count)

    @staticmethod
    def _copy(source: Path, dest: Path) -> RecordWrite:
        """Copy *source* to a sibling temp then atomically rename onto *dest*.

        The byte count is taken from the temp *before* the rename (the commit
        point); the ephemeral-source cleanup afterwards is best-effort, so a
        failure past the commit never turns a completed write into a failure.
        """
        # Write THROUGH the descriptor mkstemp returned (0600, O_EXCL) -- never
        # close it and reopen the temp by name. The name could be swapped for a
        # symlink between close and reopen (TOCTOU); writing to the fd targets
        # the exact inode mkstemp created.
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".mp3.tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
                shutil.copyfileobj(src, dst)
            byte_count = tmp.stat().st_size
            tmp.replace(dest)  # commit point -- the write is complete after this
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

        with contextlib.suppress(OSError):
            source.unlink(missing_ok=True)
        return RecordWrite(path=dest, byte_count=byte_count)
