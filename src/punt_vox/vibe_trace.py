"""Append vibe-observability traces to a durable, greppable per-user log file.

The vibe subsystem spans separate processes -- the ``mic`` MCP server and the
short-lived ``UserPromptSubmit`` hook -- that each need to prove a link fired
(nudge -> vibe set -> music re-pool). Claude Code discards MCP-server and hook
stderr, so a stderr trace is unreachable at runtime. This sink writes each
trace as one line to ``<state>/logs/vibe-trace.log`` -- a path both processes
resolve identically and a human can ``grep``.

Concurrency: every :meth:`VibeTraceLog.record` opens the file with
``O_APPEND | O_CREAT`` and writes the whole line in a single ``os.write``.
POSIX guarantees an ``O_APPEND`` write seeks to end and writes with no
intervening modification, so lines from concurrent processes never interleave
or tear -- no lock, no long-lived shared handle.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Self, final

from punt_vox.paths import log_dir

__all__ = ["VibeTraceLog"]

logger = logging.getLogger(__name__)

_PREFIX = "[vibe-trace]"
_LOG_NAME = "vibe-trace.log"
_FILE_MODE = 0o600  # per-user private state, matching the rest of <state>
_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT


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

    def record(self, event: str) -> None:
        """Append one ``[vibe-trace] {event}`` line atomically; never raise.

        A single ``os.write`` of the whole ``O_APPEND`` line is atomic against
        concurrent appenders, so cross-process lines never interleave. An I/O
        failure is logged and swallowed so a trace can never crash the
        vibe/nudge path it merely observes.
        """
        line = f"{_PREFIX} {event}\n".encode()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, _OPEN_FLAGS, _FILE_MODE)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("vibe-trace: cannot append to %s: %s", self._path, exc)
