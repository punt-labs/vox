"""Keep arbitrary exception values out of Lux boundary diagnostics."""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Self, final


@final
class LuxFaultBoundary:
    """Suppress ordinary boundary faults with a fixed, metadata-only diagnostic."""

    __slots__ = ("_failed", "_level", "_logger", "_message")
    _failed: bool
    _level: int
    _logger: logging.Logger
    _message: str

    def __new__(
        cls, logger: logging.Logger, message: str, level: int = logging.ERROR
    ) -> Self:
        """Bind a fixed diagnostic and severity, never exception text."""
        self = super().__new__(cls)
        self._logger = logger
        self._message = message
        self._level = level
        self._failed = False
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if not isinstance(exc, Exception):
            return False
        self._failed = True
        self._logger.log(self._level, "[lux] %s", self._message)
        return True

    def failed(self) -> bool:
        """Return whether this operation ended in a suppressed ordinary fault."""
        return self._failed
