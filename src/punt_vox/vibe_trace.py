"""Append vibe-observability traces to a durable, greppable per-user log file.

The vibe subsystem spans separate processes -- the ``mic`` MCP server and the
short-lived ``UserPromptSubmit`` hook -- that each need to prove a link fired
(nudge -> vibe set -> music re-pool). Claude Code discards MCP-server and hook
stderr, so a stderr trace is unreachable at runtime. This sink writes each
trace as one line to ``<state>/logs/vibe-trace.log`` -- a path both processes
resolve identically and a human can ``grep``.

Concurrency: every :meth:`VibeTraceLog.record` opens the file with
``O_APPEND | O_CREAT`` and appends the whole line in a *single* ``os.write``.
POSIX guarantees an ``O_APPEND`` write seeks to end and appends atomically, and
a trace line is far shorter than the platform's atomic-append size, so lines
from concurrent processes never interleave -- no lock, no long-lived shared
handle. Atomicity is chosen over completeness: for this two-writer proof log a
torn (interleaved) line is worse than a lost one. A short write -- in practice
only ``ENOSPC``, since Python auto-retries ``EINTR`` -- is therefore *not*
re-issued: looping to append the remainder would be a second, separate atomic
append that another writer's line could land between, tearing the very line
``O_APPEND`` protects. Instead a short count is surfaced as the ``OSError``
:meth:`record` already routes to the log, so a full disk is a diagnosable
failure in ``tts.log`` rather than a silently torn append.
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from typing import Self, TypedDict, final

from punt_vox.paths import log_dir
from punt_vox.private_state import PrivateState

__all__ = ["TraceHealth", "VibeTraceLog"]

logger = logging.getLogger(__name__)


class TraceHealth(TypedDict):
    """The sink's status-API view: where it writes and whether it can right now."""

    path: str
    writable: bool


_PREFIX = "[vibe-trace]"
_LOG_NAME = "vibe-trace.log"
_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT

# Escape every C0 control char (and DEL) so an MCP-controlled style/name -- which
# ``canonical_tag`` only end-trims -- can neither forge a second ``[vibe-trace]``
# line via an embedded newline nor corrupt a terminal via a raw control byte on
# ``cat``. Escaping, not stripping, keeps the smuggled bytes visible in the trail.
_CONTROL_ESCAPES = {ord("\t"): "\\t", ord("\n"): "\\n", ord("\r"): "\\r"}

# The C0/C1 code points ``str.splitlines()`` treats as line breaks beyond ``\n``:
# NEL (U+0085), LINE SEPARATOR (U+2028), PARAGRAPH SEPARATOR (U+2029). The file
# holds a single ``\n``, but a tool that splits on Unicode boundaries would render
# a smuggled one of these as a second visual record. Escape them too, using a
# 4-hex ``\uXXXX`` form so the two astral-plane separators round-trip.
_UNICODE_LINE_SEPARATORS = (0x85, 0x2028, 0x2029)
_SANITIZE_TABLE = {
    cp: _CONTROL_ESCAPES.get(cp, f"\\x{cp:02x}") for cp in (*range(0x20), 0x7F)
}
_SANITIZE_TABLE.update({cp: f"\\u{cp:04x}" for cp in _UNICODE_LINE_SEPARATORS})


@final
class VibeTraceLog:
    """An append-only sink for the subsystem's ``[vibe-trace]`` proof lines."""

    __slots__ = ("_guard", "_path")

    _path: Path
    _guard: PrivateState

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._guard = PrivateState(path)
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
        anchor = self._guard.nearest_existing_ancestor()
        return anchor.is_dir() and os.access(anchor, os.W_OK | os.X_OK)

    def record(self, event: str) -> None:
        """Append one sanitized ``[vibe-trace] {event}`` line; never raise.

        *event* is escaped (see :data:`_SANITIZE_TABLE`) so the record is exactly
        one physical line no control byte can forge or corrupt. The line is
        appended in a single ``O_APPEND`` ``os.write`` so concurrent writers
        never interleave. A short write is not looped -- that would tear the
        line under concurrency; the partial count is raised as an ``OSError``,
        logged, and swallowed, so a trace never crashes the path it observes.
        """
        safe = event.translate(_SANITIZE_TABLE)
        line = f"{_PREFIX} {safe}\n".encode()
        try:
            self._guard.ensure_private_tree()
            fd = self._guard.open_private(_OPEN_FLAGS)
            try:
                # One atomic O_APPEND write no concurrent writer can tear. A short
                # count -- only ENOSPC in practice, since Python auto-retries
                # EINTR -- is raised, not looped: re-issuing the remainder would be
                # a second append another writer's line could split. Raising routes
                # it to the OSError handler, so a full disk is a diagnosable
                # tts.log failure rather than a silently torn line.
                if (written := os.write(fd, line)) != len(line):
                    msg = f"short append: {written} of {len(line)} bytes (disk full?)"
                    raise OSError(errno.ENOSPC, msg, str(self._path))
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("vibe-trace: cannot append to %s: %s", self._path, exc)
