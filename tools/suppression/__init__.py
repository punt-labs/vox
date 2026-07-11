"""Count lint/type suppressions in a Python codebase and enforce a ratchet."""

from __future__ import annotations

from .baseline import SuppressionBaseline
from .cli import Cli, Options, main
from .outcome import Outcome
from .patterns import FileSuppressions
from .pyproject import PerFileIgnoresCounter
from .report import SuppressionReport
from .scanner import Scanner

__all__ = [
    "Cli",
    "FileSuppressions",
    "Options",
    "Outcome",
    "PerFileIgnoresCounter",
    "Scanner",
    "SuppressionBaseline",
    "SuppressionReport",
    "main",
]
