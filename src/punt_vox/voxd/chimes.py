"""Chime signal resolution -- maps signal names to bundled audio assets."""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path
from typing import ClassVar, Self

__all__ = ["ChimeResolver"]

logger = logging.getLogger(__name__)

_CHIME_MAP: dict[str, str] = {
    "done": "chime_done.mp3",
    "prompt": "chime_prompt.mp3",
    "acknowledge": "chime_done.mp3",
    "compact": "chime_done.mp3",
    "subagent": "chime_done.mp3",
    "farewell": "chime_done.mp3",
    "tests-pass": "chime_tests_pass.mp3",
    "tests-fail": "chime_tests_fail.mp3",
    "lint-pass": "chime_lint_pass.mp3",
    "lint-fail": "chime_lint_fail.mp3",
    "git-push-ok": "chime_git_push_ok.mp3",
    "merge-conflict": "chime_merge_conflict.mp3",
}


class ChimeResolver:
    """Resolve chime signal names to bundled asset paths."""

    __slots__ = ()

    _CHIME_MAP: ClassVar[dict[str, str]] = _CHIME_MAP

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def resolve(self, signal: str) -> Path | None:
        """Resolve a chime signal name to a bundled asset path."""
        filename = self._CHIME_MAP.get(signal)
        if filename is None:
            return None
        try:
            ref = importlib.resources.files("punt_vox.assets").joinpath(filename)
            # as_file returns a context manager; we need the actual path.
            # For installed packages the file is already on disk.
            path = Path(str(ref))
            if path.exists():
                return path
        except (TypeError, FileNotFoundError):
            pass
        return None
