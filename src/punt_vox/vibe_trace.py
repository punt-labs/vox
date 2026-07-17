"""Append vibe-observability traces to a durable, greppable per-user log file.

The vibe subsystem spans separate processes -- the ``mic`` MCP server and the
short-lived ``UserPromptSubmit`` hook -- that each need to prove a link fired
(nudge -> vibe set -> music re-pool). Claude Code discards MCP-server and hook
stderr, so a stderr trace is unreachable at runtime. This sink writes each
trace as one line to ``<state>/logs/vibe-trace.log`` -- a path both processes
resolve identically and a human can ``grep``.

Concurrency: every :meth:`VibeTraceLog.record` opens the file with
``O_APPEND | O_CREAT`` and drains the whole line to the fd before closing.
POSIX guarantees an ``O_APPEND`` write seeks to end and writes with no
intervening modification, and a trace line is far shorter than the platform's
atomic-append size, so in practice each record lands in one atomic write and
lines from concurrent processes never interleave -- no lock, no long-lived
shared handle. The drain loop is the correctness backstop: ``os.write`` may
write fewer bytes than requested without raising (a short write on ``ENOSPC``
fills the disk, returns a partial count, and only the *next* call raises), so
writing a fragment and calling it done would tear the very line ``O_APPEND``
protects. Looping until the buffer is empty makes a genuine ``ENOSPC`` surface
as the ``OSError`` :meth:`record` already swallows instead of a torn append.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Self, TypedDict, final

from punt_vox.paths import log_dir

__all__ = ["TraceHealth", "VibeTraceLog"]

logger = logging.getLogger(__name__)


class TraceHealth(TypedDict):
    """The sink's status-API view: where it writes and whether it can right now."""

    path: str
    writable: bool


_PREFIX = "[vibe-trace]"
_LOG_NAME = "vibe-trace.log"
_FILE_MODE = 0o600  # per-user private state, matching the rest of <state>
_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT

# Escape every C0 control char (and DEL) so an MCP-controlled style/name -- which
# ``canonical_tag`` only end-trims -- can neither forge a second ``[vibe-trace]``
# line via an embedded newline nor corrupt a terminal via a raw control byte on
# ``cat``. Escaping, not stripping, keeps the smuggled bytes visible in the trail.
_CONTROL_ESCAPES = {ord("\t"): "\\t", ord("\n"): "\\n", ord("\r"): "\\r"}
_SANITIZE_TABLE = {
    cp: _CONTROL_ESCAPES.get(cp, f"\\x{cp:02x}") for cp in (*range(0x20), 0x7F)
}


@final
class VibeTraceLog:
    """An append-only sink for the subsystem's ``[vibe-trace]`` proof lines."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @classmethod
    def default(cls) -> Self:
        """Return the sink writing to ``<state>/logs/vibe-trace.log``."""
        return cls(log_dir() / _LOG_NAME)

    @property
    def path(self) -> Path:
        """Return the file path clients ``grep`` for ``[vibe-trace]`` lines."""
        return self._path

    def health(self) -> TraceHealth:
        """Return the sink's path and live writability for the status API.

        This is the client-observable answer to "is the proof-trail itself
        working?" -- surfaced through ``mic:status`` so a broken sink is
        queryable, never buried in a second daemon log.
        """
        return {"path": str(self._path), "writable": self.is_writable()}

    def is_writable(self) -> bool:
        """Return whether a trace could be appended to the log right now.

        Never raises. A health check must not be able to crash the surface
        that reports it -- ``mic:status`` calls this unguarded -- so any
        :class:`OSError` from probing the real filesystem is fail-safe: when a
        traversal-permission failure on an intermediate ancestor (or any other
        stat error) makes writability impossible to confirm, report ``False``.
        ``Path.exists``/``Path.is_file`` already swallow a missing path, but
        not an unreadable ancestor directory; this guard closes that gap.
        """
        try:
            return self._probe_writable()
        except OSError:
            return False

    def _probe_writable(self) -> bool:
        """Return real-filesystem writability; :meth:`is_writable` guards this.

        Health is a property of the *path*, not of any one process's last
        write: two separate processes -- the ``mic`` server and the hook --
        append here, so trusting a single writer's success would report a
        peer's state. This probes the real filesystem instead. An existing
        log must be a writable regular file; a not-yet-created log needs its
        nearest existing ancestor to grant both write and search (``W_OK |
        X_OK``) -- creating a file needs the search bit, so ``--w-------`` fails.
        """
        if self._path.exists():
            return self._path.is_file() and os.access(self._path, os.W_OK)
        anchor = self._nearest_existing_ancestor()
        return anchor.is_dir() and os.access(anchor, os.W_OK | os.X_OK)

    def record(self, event: str) -> None:
        """Append one sanitized ``[vibe-trace] {event}`` line; never raise.

        *event* is escaped (see :data:`_SANITIZE_TABLE`) so the record is exactly
        one physical line no control byte can forge or corrupt. The whole
        ``O_APPEND`` line is then drained to the fd before it closes, so a short
        write can never leave a newline-less fragment that the next append glues
        onto -- the tear ``O_APPEND`` alone does not prevent. An I/O failure is
        logged and swallowed so a trace never crashes the path it observes.
        """
        safe = event.translate(_SANITIZE_TABLE)
        buf = memoryview(f"{_PREFIX} {safe}\n".encode())
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, _OPEN_FLAGS, _FILE_MODE)
            try:
                while buf:
                    buf = buf[os.write(fd, buf) :]
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("vibe-trace: cannot append to %s: %s", self._path, exc)

    def _nearest_existing_ancestor(self) -> Path:
        """Return the closest existing directory above the (absent) log file."""
        return next(p for p in self._path.parents if p.exists())
