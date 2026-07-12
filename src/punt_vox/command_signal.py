"""Classify a finished bash command into a vibe signal."""

from __future__ import annotations

import re
from typing import ClassVar, Self, final


@final
class CommandSignal:
    """A finished bash command, classifiable into a vibe signal.

    A structured failure verdict wins over the exit code, because a piped
    command (``pytest | tee``, ``make test || true``) can exit 0 while its
    tail still says ``2 failed``. Every classification scans the anchored
    failure markers first; only their absence lets a success signal stand.
    The markers key on structured summary tokens (pytest's ``N passed`` /
    ``N failed``, ruff's ``Found N error``) scanned from the output tail —
    an incidental ``error`` or ``failed`` in a path, commit message, or PR
    title cannot manufacture a false failure, and ``0 failed`` is a pass.
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
        (
            "git-push-ok",
            re.compile(
                r"Everything up-to-date"
                r"|\[new branch\]"
                r"|[0-9a-f]{7,40}\.\.[0-9a-f]{7,40}"
                r"|-> \S*(?:refs|origin)/"
            ),
        ),
        (
            "git-commit",
            re.compile(r"^\[\S+ [0-9a-f]{7,40}\] |^create mode", re.MULTILINE),
        ),
        ("pr-created", re.compile(r"pull/\d+|created pull request")),
    )

    _FAILURE_MARKERS: ClassVar[tuple[tuple[str, re.Pattern[str]], ...]] = (
        ("lint-fail", re.compile(r"Found \d+ error")),
        ("tests-fail", re.compile(r"\b[1-9]\d* failed\b|^FAILED ", re.MULTILINE)),
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

        A structured failure verdict in the tail wins over any exit code —
        this catches ``pytest | tee`` and ``make test || true``, which exit
        0 while the output still says ``2 failed``. Absent a failure, a
        success marker stands. When nothing is recognized, a nonzero exit
        yields the honest ``cmd-fail``; a zero or missing exit yields None.
        """
        failure = self._first_match(self._FAILURE_MARKERS)
        if failure is not None:
            return failure
        success = self._first_match(self._SUCCESS_MARKERS)
        if success is not None:
            return success
        if self._exit_code not in (None, 0):
            return "cmd-fail"
        return None

    def _first_match(
        self, markers: tuple[tuple[str, re.Pattern[str]], ...]
    ) -> str | None:
        """Return the signal of the first marker whose pattern hits the tail."""
        for signal, pattern in markers:
            if pattern.search(self._tail):
                return signal
        return None
