"""What voxd published in its run files: the port, the publisher's pid, and whose.

``vox daemon status`` may ask voxd for health before ``lsof``/``fuser`` have
attributed the port, but only through what voxd itself published: the
``serve.port`` and ``serve.pid`` run files, written by atomic rename when it
binds. This class reads both once and says who may be asked: this user's own
``serve.port`` naming the service port, and this user's own ``serve.pid``
naming a process that is still alive and this user's. A port file a crash
left behind names a pid that is gone, so whatever binds the port next is
never sent the token. When the gate closes it says which reading closed it.

Each file's text and owner come from one open descriptor: the bytes read and
the ``fstat`` of the file they were read from. A separate ``stat`` of the
path could describe a file voxd renamed into place after the read, and then
the uid trusted would not be the uid of the bytes trusted. The two files are
replaced by separate renames, so the pair is checked too: voxd publishes
``serve.pid`` before ``serve.port``, and the reader reads the port, then the
pid, then the port again and trusts the pair only when the port file is
still the one it read first. A restart that lands mid-read is therefore
never a new port paired with the previous daemon's pid. The reader never
takes voxd's lock and never changes a file.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.client_port import PortText
from punt_vox.file_identity import FileIdentity
from punt_vox.service.backend import PortProbeError
from punt_vox.service.not_asked import NotAskedError

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PublishedPort"]


@final
class PublishedPort:
    """One reading of ``serve.port`` and ``serve.pid``: port, pid, and whose."""

    __slots__ = ("_pid", "_port", "_refusal")

    PID_FILE: ClassVar[str] = "serve.pid"
    # pid_t is a signed 32-bit int on every platform voxd runs on; a larger
    # number is not a pid, and os.kill would raise OverflowError on it. The
    # digit count is checked before int(), which refuses very long strings.
    PID_MAX: ClassVar[int] = 2**31 - 1
    PID_DIGITS: ClassVar[int] = len(str(PID_MAX))

    _port: int | None  # None: no port file, or one that does not name a port
    _pid: int | None  # None: no pid file, or one that does not name a pid
    _refusal: NotAskedError | None  # None: both files are this user's

    def __new__(
        cls,
        port: int | None,
        pid: int | None = None,
        *,
        refusal: NotAskedError | None = None,
    ) -> Self:
        """Create a reading: *port* and *pid* as published; *refusal* why not ours."""
        self = super().__new__(cls)
        self._port = port
        self._pid = pid
        self._refusal = refusal
        return self

    @classmethod
    def at(cls, port_file: Path) -> Self:
        """Read ``serve.port`` at *port_file* and ``serve.pid`` beside it.

        Raises:
            PortProbeError: If a file is there but cannot be read or statted.
        """
        read = cls._owned_text(port_file)
        if read is None:
            return cls(None, refusal=NotAskedError.unpublished())
        text, uid, seen = read
        try:
            port = PortText.parse(text)
        except ValueError as exc:
            logger.warning("Ignoring stale %s: %s", port_file, exc)
            return cls(None, refusal=NotAskedError.unparseable())
        refusal = cls._foreign(port_file.name, uid)
        if refusal is not None:
            return cls(port, refusal=refusal)
        reading = cls._with_pid(port, port_file.with_name(cls.PID_FILE))
        return reading if cls._still(port_file, seen) else cls._republished(port)

    @classmethod
    def _with_pid(cls, port: int, pid_file: Path) -> Self:
        """Return the reading for *port* once *pid_file* is read the same way."""
        read = cls._owned_text(pid_file)
        if read is None:
            return cls(port, refusal=NotAskedError.no_pid())
        text, uid, _ = read
        if not cls._is_pid(text):
            return cls(port, refusal=NotAskedError.pid_unparseable())
        return cls(port, int(text), refusal=cls._foreign(pid_file.name, uid))

    @classmethod
    def _still(cls, port_file: Path, seen: FileIdentity) -> bool:
        """Return True when *port_file* is still the file read as *seen*."""
        again = cls._owned_text(port_file)
        return again is not None and again[2] == seen

    @classmethod
    def _republished(cls, port: int) -> Self:
        """Return the reading for a port file replaced while the pair was read."""
        return cls(port, refusal=NotAskedError.republished())

    @classmethod
    def _is_pid(cls, text: str) -> bool:
        """Return True for a decimal pid in 1..PID_MAX; long text is never converted."""
        return (
            text.isdecimal()
            and len(text) <= cls.PID_DIGITS
            and 0 < int(text) <= cls.PID_MAX
        )

    @staticmethod
    def _foreign(name: str, uid: int) -> NotAskedError | None:
        """Return why a file *name* owned by *uid* is not this user's; else ``None``."""
        return None if uid == os.getuid() else NotAskedError.not_owned(name, uid)

    @staticmethod
    def _owned_text(path: Path) -> tuple[str, int, FileIdentity] | None:
        """Return *path*'s text, owner uid and identity from one open file.

        ``None`` is the documented answer when no file is there.

        Raises:
            PortProbeError: If the file is there but cannot be read or statted.
        """
        try:
            with path.open("rb") as handle:
                try:
                    st = os.fstat(handle.fileno())
                except OSError as exc:
                    msg = f"cannot stat {path}: {exc}"
                    raise PortProbeError(msg) from exc
                text = handle.read().decode("utf-8").strip()
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            msg = f"cannot read {path}: invalid UTF-8"
            raise PortProbeError(msg) from None
        except OSError as exc:
            msg = f"cannot read {path}: {exc}"
            raise PortProbeError(msg) from exc
        return text, st.st_uid, FileIdentity.of(st)

    @property
    def port(self) -> int | None:
        """Return the port the file named; ``None`` when none is published."""
        return self._port

    @property
    def pid(self) -> int | None:
        """Return the pid the file named; ``None`` when none is published."""
        return self._pid

    def publisher(self, service_port: int) -> int:
        """Return the live pid that published *service_port*, or raise why not.

        Only this user's own files, naming *service_port* and a pid that is
        alive and this user's, open the gate; each other reading raises the
        ``NotAskedError`` that names it.

        Raises:
            NotAskedError: If the files do not name this user's live voxd on
                *service_port*.
        """
        if self._refusal is not None:
            raise self._refusal
        if self._port is None:
            raise NotAskedError.unparseable()
        if self._port != service_port:
            raise NotAskedError.other_port(self._port)
        if self._pid is None:
            # Never 0: os.kill(0, 0) signals the caller's own process group
            # and succeeds, which would open the gate with no publisher at all.
            raise NotAskedError.no_pid()
        pid = self._pid
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise NotAskedError.publisher_gone(pid) from None
        except PermissionError:
            raise NotAskedError.publisher_foreign(pid) from None
        return pid
