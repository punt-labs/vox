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

import os
import stat
from dataclasses import dataclass
from itertools import batched
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.recording_name import RecordingName
from punt_vox.voxd.containment import ContainmentRoot
from punt_vox.voxd.path_status import PathStatus
from punt_vox.voxd.record_target import RecordTarget, RecordWrite

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["RecordStore", "RecordWrite"]

_NAME_LABEL = "recording name"
_SCAN_BATCH_SIZE = 128


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

    __slots__ = ("_containment",)

    _containment: ContainmentRoot

    def __new__(cls, root: Path) -> Self:
        """Create a record store."""
        self = super().__new__(cls)
        self._containment = ContainmentRoot(root, _NAME_LABEL)
        return self

    @property
    def root(self) -> Path:
        """Return the recordings root every write is contained within."""
        return self._containment.root

    def resolve(self, name: str | None, text: str) -> Path:
        """Resolve the destination for a record write, contained in the root.

        A client-supplied *name* is validated as a :class:`RecordingName`; only
        ``None`` (absent) content-addresses by *text* -- the canonical name every
        other vox MP3 uses. An explicit empty or whitespace-only string is an
        invalid name, not "absent", so it is rejected (``ValueError``), as are
        absolute, separated, traversing, NUL-bearing, and overlong names. If
        necessary, create the private store so the filesystem can check the name.
        """
        return self._resolve_target(name, text).path

    def _resolve_target(self, name: str | None, text: str) -> RecordTarget:
        """Return a contained, filesystem-checked target for a new recording."""
        dest = self._containment.resolve(RecordingName.for_take(name, text).value)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = RecordTarget(dest)
        target.check_name()
        return target

    def resolve_ref(self, ref: str) -> Path:
        """Resolve a play/fetch reference to a contained store path.

        References require a bare name and no path escape, not the additional
        rules for naming a new take.
        The caller checks existence -- an unknown but well-formed name resolves
        to a path inside the root that simply does not exist yet.
        """
        return self._containment.resolve(ref)

    def place(self, *, source: Path, text: str, name: str | None) -> RecordWrite:
        """Land *source* at its contained destination atomically; return path + bytes.

        The owned source is moved with an atomic rename, falling back to a
        copy only on cross-filesystem ``EXDEV``. Either way the destination
        is replaced atomically, never with a partial write.
        """
        target = self._resolve_target(name, text)
        self.root.chmod(0o700)
        return target.place(source)

    def entry_batches(self) -> Iterator[list[RecordingEntry]]:
        """Yield bounded batches of immediate, non-symlink recording files.

        A missing root is empty. An inaccessible root raises OSError.
        """
        if not PathStatus.of(self.root).is_directory:
            return
        with os.scandir(self.root) as children:
            for batch in batched(children, _SCAN_BATCH_SIZE, strict=False):
                yield [
                    entry
                    for child in batch
                    if (entry := self._entry_for(Path(child.path))) is not None
                ]

    def _entry_for(self, child: Path) -> RecordingEntry | None:
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
            self._containment.contained_child(child.name)
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
        path = self._containment.contained_child(ref)
        # A no-follow classify accepts a link entry (even a broken one) so its
        # removal deletes the link, and a real recording; a directory or an absent
        # name matches neither and is "not found". Classifying from one stat means
        # a ``PermissionError`` surfaces rather than reading as a false "absent".
        status = PathStatus.of(path, follow_symlinks=False)
        if not (status.is_symlink or status.is_regular_file):
            msg = f"no recording named {ref!r}"
            raise FileNotFoundError(msg)
        path.unlink()
