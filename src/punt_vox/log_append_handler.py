"""A logging handler that appends every record directly to one shared vox.log.

Every process -- the daemon and every transient client (MCP server, hook, CLI,
detached playback) -- installs this handler, so all records land in one file by a
local ``O_APPEND`` write with no socket and no daemon round-trip (DES-017). The
handler renders the record with the shared :data:`LOG_FORMAT` and hands the line
to a shared :class:`AtomicAppendLog`, whose ``flock``-guarded rotation and 0600
enforcement it inherits -- so both entry points share one writer path and one
rotation mechanism (never two racing on the file).

``emit`` never raises and never re-enters ``logging``: a ``getMessage``
mis-format goes to ``handleError`` (logging's own last-resort sink), and the
sink's own I/O errors go to ``sys.__stderr__``. The record is escaped exactly
once -- by the sink, not here -- so a newline or control byte in any field
(a client-shipped name, a provider error body) stays one physical line.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Self, final

from punt_vox.append_log import AtomicAppendLog
from punt_vox.log_format import LOG_DATE_FORMAT, LOG_FORMAT

__all__ = ["AppendLogHandler"]


@final
class AppendLogHandler(logging.Handler):
    """Root handler that appends each formatted record to the shared vox.log sink."""

    _sink: AtomicAppendLog
    _name_prefix: str

    @classmethod
    def bind(
        cls, sink: AtomicAppendLog, *, name_prefix: str = "", level: str = "INFO"
    ) -> Self:
        """Build a handler over *sink*, prefixing each logger name for its origin.

        A factory, not a constructor override: ``logging.Handler.__init__`` owns
        the handler machinery (level, lock, filters), so the sink and prefix attach
        after the parent initialises. *name_prefix* is ``""`` for the daemon
        (records keep their own logger name) and ``"client.<role>."`` for a client,
        so a client line greps apart from a daemon line.
        """
        handler = cls()
        handler._sink = sink
        handler._name_prefix = name_prefix
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        return handler

    @classmethod
    def for_file(
        cls, *, filename: str, name_prefix: str = "", level: str = "INFO"
    ) -> Self:
        """dictConfig ``()`` factory: bind a handler to an ``AtomicAppendLog``."""
        return cls.bind(
            AtomicAppendLog(Path(filename)), name_prefix=name_prefix, level=level
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Render *record* to one line and append it; never raise, never log.

        The logger name is prefixed for the render only and restored in
        ``finally``, so a client line carries its origin without mutating the
        shared record for any later handler. This is a ``logging`` system boundary
        (PY-EH-6): a broken record -- a bad ``%``-arg, a raising ``__str__``, a lone
        surrogate -- must degrade to ``handleError`` (logging's own last-resort
        sink), never crash the caller that logged. ``RecursionError`` is re-raised
        so a genuine stack overflow is never masked. The render and the sink append
        are both inside the guard, so a fault in either is absorbed identically.
        """
        original = record.name
        try:
            record.name = f"{self._name_prefix}{original}"
            self._sink.append(self.format(record))
        except RecursionError:
            raise
        except Exception:  # noqa: BLE001 -- logging boundary (PY-EH-6): never crash the logger
            self.handleError(record)
        finally:
            record.name = original
