"""Remove an abandoned synthesis's output file once its worker thread is done."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.log_sanitize import SANITIZER

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path

    from punt_vox.types import AudioResult

__all__ = ["OutputDiscard"]

logger = logging.getLogger(__name__)


@final
class OutputDiscard:
    """A done-callback that removes the output file after its writer finishes.

    A cancelled request cannot stop its worker thread, which may still write
    the file, so the file goes when the thread's future completes. The callback
    also retrieves the thread's exception, so a provider failure after the
    cancel is not reported as never retrieved. It logs the type and message,
    never the repr: an encoding error's repr quotes the value it could not
    encode, which can be the API key. The message is escaped onto one line: it
    can carry caller text, such as a voice name, and a raw newline there would
    forge a log record.

    The file goes whatever the log line does. The message is rendered lazily,
    inside logging, whose error handler absorbs an exception whose ``__str__``
    raises; the ``finally`` covers whatever still escapes.
    """

    __slots__ = ("_output_path",)
    _output_path: Path

    def __new__(cls, output_path: Path) -> Self:
        self = super().__new__(cls)
        self._output_path = output_path
        return self

    def __call__(self, done: asyncio.Future[AudioResult]) -> None:
        """Log *done*'s failure, if any, then remove the output file."""
        try:
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.debug(
                    "cancelled synthesis then failed: %s: %s",
                    type(error).__name__,
                    _EscapedMessage(error),
                )
        finally:
            self._output_path.unlink(missing_ok=True)

    def __repr__(self) -> str:
        return f"OutputDiscard({self._output_path!s})"


@final
class _EscapedMessage:
    """An exception's message, rendered and escaped only when logging formats it.

    Deferring ``str()`` to format time keeps a raising ``__str__`` inside
    logging's error handling instead of the callback's frame.
    """

    __slots__ = ("_error",)
    _error: BaseException

    def __new__(cls, error: BaseException) -> Self:
        self = super().__new__(cls)
        self._error = error
        return self

    def __str__(self) -> str:
        return SANITIZER.escape(str(self._error))

    def __repr__(self) -> str:
        return f"_EscapedMessage({type(self._error).__name__})"
