"""Describe failed git commands for a log line and a worker record, paths scrubbed."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
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


@final
class DiscardError(Exception):
    """Every step of a discard that failed, each named and scrubbed.

    A discard attempts all of its steps so one refusal (a locked worktree,
    a busy directory) does not leave the later artefacts behind; this is
    raised at the end when any step failed.
    """

    __slots__ = ("_steps",)
    _steps: list[tuple[str, GitFailure]]

    def __new__(cls, *args: object) -> Self:
        """Start with no failed step; ``attempt`` records each one that fails."""
        self = super().__new__(cls, *args)
        self._steps = []
        return self

    def attempt(self, step: str, run: Callable[[], None]) -> None:
        """Run one step, keeping its failure under *step* instead of raising."""
        try:
            run()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self._steps.append((step, GitFailure(exc)))

    def __bool__(self) -> bool:
        return bool(self._steps)

    def __str__(self) -> str:
        return "; ".join(f"{step}: {failure}" for step, failure in self._steps)

    def __repr__(self) -> str:
        return f"DiscardError({self._steps!r})"
