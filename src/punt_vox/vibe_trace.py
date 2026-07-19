"""Append vibe-observability traces to a durable, greppable per-user log file.

The vibe subsystem spans separate processes -- the ``mic`` MCP server and the
short-lived ``UserPromptSubmit`` hook -- that each need to prove a link fired
(nudge -> vibe set -> music re-pool). Claude Code discards MCP-server and hook
stderr, so a stderr trace is unreachable at runtime. This sink writes each trace
as one line to ``<state>/logs/vibe-trace.log`` -- a path both processes resolve
identically and a human can ``grep``.

:class:`VibeTraceLog` composes :class:`AtomicAppendLog`, the shared multi-writer
sink: it inherits the single-``os.write`` ``O_APPEND`` atomicity (concurrent
writers never interleave), the 0600 private-file guarantee, the SANITIZER escape,
and size-capped rotation. This module adds only the ``[vibe-trace]`` prefix and
the :meth:`~VibeTraceLog.health` view ``mic:status`` reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, TypedDict, final

from punt_vox.append_log import AtomicAppendLog
from punt_vox.paths import log_dir

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["TraceHealth", "VibeTraceLog"]


class TraceHealth(TypedDict):
    """The sink's status-API view: where it writes and whether it can right now."""

    path: str
    writable: bool


_PREFIX = "[vibe-trace]"
_LOG_NAME = "vibe-trace.log"


@final
class VibeTraceLog:
    """An append-only sink for the subsystem's ``[vibe-trace]`` proof lines."""

    __slots__ = ("_sink",)

    _sink: AtomicAppendLog

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._sink = AtomicAppendLog(path)
        return self

    @classmethod
    def default(cls) -> Self:
        """Return the sink writing to ``<state>/logs/vibe-trace.log``."""
        return cls(log_dir() / _LOG_NAME)

    @property
    def path(self) -> Path:
        """Return the file path clients ``grep`` for ``[vibe-trace]`` lines."""
        return self._sink.path

    def health(self) -> TraceHealth:
        """Return the sink's path and live writability for the status API.

        This is the client-observable answer to "is the proof-trail itself
        working?" -- surfaced through ``mic:status`` so a broken sink is
        queryable, never buried in a second daemon log.
        """
        return {"path": str(self._sink.path), "writable": self._sink.is_writable()}

    def is_writable(self) -> bool:
        """Return whether a trace could be appended to the log right now."""
        return self._sink.is_writable()

    def record(self, event: str) -> None:
        """Append one sanitized ``[vibe-trace] {event}`` line; never raise.

        The shared sink escapes *event* and appends it in a single ``O_APPEND``
        write, so concurrent writers never interleave and no control byte can
        forge a second line. A write failure swallows to ``sys.__stderr__`` inside
        the sink -- never through ``logging`` -- so a trace never crashes the path
        it observes.
        """
        self._sink.append(f"{_PREFIX} {event}")
