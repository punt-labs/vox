"""Classify a finished bash command into a vibe signal."""

from __future__ import annotations

import re
from typing import ClassVar, Self, final


@final
class CommandSignal:
    """A finished bash command, classifiable into a vibe signal.

    Exit code is authoritative: a zero exit never yields a failure signal
    and a nonzero exit never yields a success signal. Recognition anchors
    to structured summary tokens (pytest's ``N passed``, ruff's ``Found N
    error``) scanned from the output tail, so an incidental ``error`` or
    ``failed`` in a file path, commit message, or PR title cannot
    manufacture a false failure.
    """

    __slots__ = ("_exit_code", "_tail")

    _exit_code: int | None
    _tail: str

    # pytest and make print their verdict at the very end of a long run;
    # scanning the head would miss it. Cap the tail to bound regex cost.
    _TAIL_CHARS: ClassVar[int] = 4000

    _SUCCESS_MARKERS: ClassVar[tuple[tuple[str, re.Pattern[str]], ...]] = (
        ("tests-pass", re.compile(r"\b\d+ passed\b")),
        ("lint-pass", re.compile(r"All checks passed|\b0 errors\b")),
        ("git-push-ok", re.compile(r"Everything up-to-date|-> .*main")),
        ("git-commit", re.compile(r"^\[.+\] .+|^create mode", re.MULTILINE)),
        ("pr-created", re.compile(r"pull/\d+|created pull request")),
    )

    _FAILURE_MARKERS: ClassVar[tuple[tuple[str, re.Pattern[str]], ...]] = (
        ("lint-fail", re.compile(r"Found \d+ error")),
        ("tests-fail", re.compile(r"\b\d+ failed\b|^FAILED ", re.MULTILINE)),
        ("merge-conflict", re.compile(r"CONFLICT")),
    )

    @classmethod
    def signal_names(cls) -> frozenset[str]:
        """Return every signal emitted from a recognized marker.

        Excludes the ``cmd-fail`` fallback, which fires on a bare nonzero
        exit with no recognized marker.
        """
        return frozenset(
            name for name, _ in (*cls._SUCCESS_MARKERS, *cls._FAILURE_MARKERS)
        )

    def __new__(cls, exit_code: int | None, stdout: str) -> Self:
        """Capture the exit code and the tail of stdout for classification."""
        self = super().__new__(cls)
        self._exit_code = exit_code
        self._tail = stdout[-cls._TAIL_CHARS :]
        return self

    def signal(self) -> str | None:
        """Return the vibe signal for this command, or None if unclassified.

        A zero exit scans only success markers — success is success, so no
        incidental ``error`` or ``failed`` can turn it into a failure. A
        nonzero exit scans only failure markers and falls back to the honest
        ``cmd-fail`` when none match. A missing exit code (the transcript
        watcher, which has text but no status) scans structured tokens
        alone, failure before success, with no ``cmd-fail`` fallback.
        """
        if self._exit_code is None:
            return self._first_match(self._FAILURE_MARKERS) or self._first_match(
                self._SUCCESS_MARKERS
            )
        if self._exit_code == 0:
            return self._first_match(self._SUCCESS_MARKERS)
        return self._first_match(self._FAILURE_MARKERS) or "cmd-fail"

    def _first_match(
        self, markers: tuple[tuple[str, re.Pattern[str]], ...]
    ) -> str | None:
        """Return the signal of the first marker whose pattern hits the tail."""
        for signal, pattern in markers:
            if pattern.search(self._tail):
                return signal
        return None
