"""Describe a failed git command for a log line and a worker record, paths scrubbed."""

from __future__ import annotations

import re
import subprocess
from typing import Final, Self, final

# An absolute path inside a git message or argv; it names the operator's
# filesystem, which neither the log nor a worker record should carry.
_ABSOLUTE_PATH: Final = re.compile(r"(?<![\w.])/[^\s'\":]+")


@final
class GitFailure:
    """One line naming the exception type, the git argv and its stderr."""

    __slots__ = ("_detail", "_kind")
    _detail: str
    _kind: str

    def __new__(cls, exc: Exception) -> Self:
        """Read the argv and stderr a subprocess error carries; else its message."""
        self = super().__new__(cls)
        self._kind = type(exc).__name__
        self._detail = cls._scrub(cls._describe(exc))
        return self

    @property
    def kind(self) -> str:
        """Return the exception class name."""
        return self._kind

    @property
    def detail(self) -> str:
        """Return the command and stderr, or the message, with paths scrubbed."""
        return self._detail

    def __str__(self) -> str:
        return f"{self._kind}: {self._detail}"

    def __repr__(self) -> str:
        return f"GitFailure({self._kind!r}, {self._detail!r})"

    @staticmethod
    def _describe(exc: Exception) -> str:
        if not isinstance(
            exc, subprocess.CalledProcessError | subprocess.TimeoutExpired
        ):
            return str(exc)
        command = " ".join(str(word) for word in exc.cmd)
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return f"{command}: {stderr}" if stderr else command

    @staticmethod
    def _scrub(text: str) -> str:
        """Collapse newlines to one line and replace every absolute path."""
        return _ABSOLUTE_PATH.sub("<path>", " ".join(text.split()))
