"""Read and rewrite a text file atomically, preserving bytes, mode, and symlinks."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Self, final

__all__ = ["AtomicFile"]


@final
class AtomicFile:
    """A text file rewritten without ever corrupting the user's content.

    Reads preserve bytes verbatim (no universal-newline translation), so a
    read/write round-trip keeps LF, CRLF, and lone-CR endings byte-identical.
    Each write lands atomically -- a temp file in the target's own directory is
    flushed, ``fsync``-ed, and ``os.replace``-d over the target, so an
    interrupted write (SIGKILL, power loss) leaves the original untouched
    rather than truncated. A symlinked path is followed to its real file so the
    rename updates the file rather than clobbering the link, and the target's
    permission mode is preserved across the replace.
    """

    __slots__ = ("_path",)

    _path: Path

    # Temp files share the target's directory so ``os.replace`` is a
    # same-filesystem atomic rename; the prefix/suffix let a failed write's
    # leftover be located and cleaned up.
    _TMP_PREFIX = ".claude-md-"
    _TMP_SUFFIX = ".tmp"
    # A brand-new file is stamped with this mode rather than the 0600 that
    # ``mkstemp`` gives the temp -- a predictable default for a config file,
    # independent of the process umask.
    _NEW_FILE_MODE = 0o644

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the managed path (the symlink itself when the path is one)."""
        return self._path

    def read(self) -> str:
        """Return the file's text verbatim, or ``""`` when it does not exist.

        ``newline=""`` disables universal-newline translation: without it
        ``read_text`` rewrites every ``\\r\\n`` and lone ``\\r`` to ``\\n`` on
        read, normalizing a CRLF (or old-Mac CR) file to LF before a caller has
        even parsed it -- silently altering content. Reading bytes as-is is what
        lets a write-then-read round-trip stay byte-identical for LF, CRLF, and
        lone-CR alike.
        """
        if not self._path.is_file():
            return ""
        return self._path.read_text(encoding="utf-8", newline="")

    def replace(self, text: str) -> None:
        """Replace the file's contents with *text* atomically.

        Write *text* to a temporary file in the target's own directory, flush
        and ``fsync`` it, then ``Path.replace`` it over the target.
        ``Path.replace`` wraps ``os.replace`` -- an atomic rename on POSIX
        (macOS and Linux, the only supported platforms) -- so an interrupted
        write leaves the original untouched rather than truncated: the user's
        hand-authored content is never at risk, only the pending replacement.

        ``os.fdopen`` takes ownership of the ``mkstemp`` descriptor *first* so
        the ``with`` block always closes it on every exit path. If ``fdopen``
        itself raises, the raw fd is closed explicitly (it never took
        ownership) -- otherwise a repeated write would leak a descriptor per
        failure. The temp file is unlinked on *any* exception before the
        rename -- ``OSError`` or otherwise -- so no leftover temp is orphaned on
        any failure path (fdopen, write, flush, fsync, chmod, or replace).
        """
        target = self._write_target()
        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=directory, prefix=self._TMP_PREFIX, suffix=self._TMP_SUFFIX
        )
        tmp = Path(tmp_name)
        try:
            # newline="" writes the bytes verbatim -- no translation of "\n" to
            # os.linesep. The rendered text already carries the user's original
            # line endings (preserved by read); translating defeats that.
            handle = os.fdopen(fd, "w", encoding="utf-8", newline="")
        except BaseException:
            os.close(fd)  # fdopen did not take ownership -- close the raw fd
            tmp.unlink(missing_ok=True)
            raise
        try:
            with handle:  # owns fd now; closes it on every exit path
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            # mkstemp creates the temp at 0600 and Path.replace preserves the
            # source's mode, so without this an existing 0644 file would silently
            # become 0600 on the first write. Match the target's current mode
            # before the rename; a brand-new file gets the default mode.
            tmp.chmod(self._replacement_mode(target))
            tmp.replace(target)
        except BaseException:
            # Any failure before the successful rename -- of any exception type,
            # not just OSError -- leaves the temp behind. Unlink it so no leftover
            # is orphaned (a raising fsync, a non-OSError chmod, a
            # KeyboardInterrupt mid-write all land here). The unlink is suppressed
            # if it itself raises, so the bare ``raise`` re-raises the real cause
            # rather than the cleanup error. Reached only before the rename: once
            # ``tmp.replace`` succeeds the temp no longer exists.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise

    def _write_target(self) -> Path:
        """Return the real path the atomic replace must rename onto.

        When ``self._path`` is a symlink -- common with dotfile managers
        (chezmoi, GNU stow, bare-repo dotfiles) that link a config file at a
        file inside their store -- ``os.replace(tmp, link)`` would replace the
        *link itself* with a regular file, breaking the dotfile setup and
        silently diverging the file from its source-of-truth target. Following
        the link to ``self._path.resolve()`` and renaming onto that real path
        instead preserves the symlink and updates the file it points at.
        ``resolve()`` also settles any symlinked parent directory, so the temp
        (created in the resolved parent by :meth:`replace`) shares the target's
        filesystem and the rename stays atomic. A non-symlink path is returned
        unchanged, preserving the original behavior exactly.
        """
        if self._path.is_symlink():
            return self._path.resolve()
        return self._path

    def _replacement_mode(self, target: Path) -> int:
        """Return the permission bits to stamp on the replacement file.

        Preserve *target*'s current mode when it already exists so an atomic
        replace never changes how the user's file is exposed. *target* is the
        resolved real file (see :meth:`_write_target`), so when ``self._path``
        is a symlink the mode is read from the link's destination -- the file
        actually being rewritten -- not the link. A brand-new file is forced to
        :data:`_NEW_FILE_MODE` (``rw-r--r--``) -- a predictable, sane default
        for a config file -- rather than the 0600 ``mkstemp`` gives the temp.
        That constant is deliberate, *not* the umask-derived mode a plain
        ``open()`` would produce (``0666 & ~umask``); a restrictive umask would
        otherwise make the created file's mode vary by environment. Called
        before the rename, while the target (if any) still exists.
        """
        if target.is_file():
            return stat.S_IMODE(target.stat().st_mode)
        return self._NEW_FILE_MODE
