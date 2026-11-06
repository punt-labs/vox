"""Resolve a path argument the way pi's own read-only tools resolve it.

A deny-list that sees ``@.env.local`` while pi reads ``.env.local`` checks the
wrong file. pi normalizes every tool path before use (``normalizePath`` in
pi's ``utils/paths``): unicode spaces become plain spaces, a leading ``@`` is
stripped, ``~`` and ``~/`` expand to the home directory, a ``file://`` URL is
decoded, and a relative path resolves against the session cwd.
:meth:`PiPath.resolve` does the same, so the secret checks on the Python side
judge the path pi will actually open. The pi-side guard extension
(``pi_extension/secret_guard.js``) mirrors the same rules in JavaScript.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import final
from urllib.parse import unquote, urlparse

__all__ = ["PiPath"]

_UNICODE_SPACES = re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]")


@final
class PiPath:
    """pi's path normalization, as a pure function of the argument and the cwd."""

    __slots__ = ()

    @classmethod
    def resolve(cls, raw: str, cwd: Path) -> Path:
        """Return *raw* as the absolute, lexically normalized path pi would open."""
        path = Path(cls.normalize(raw))
        if not path.is_absolute():
            path = cwd / path
        return Path(os.path.normpath(path))

    @staticmethod
    def normalize(raw: str) -> str:
        """Apply pi's ``normalizePath``: spaces, ``@``, ``~``, ``file://``."""
        text = _UNICODE_SPACES.sub(" ", raw)
        text = text.removeprefix("@")
        if text == "~":
            return str(Path.home())
        if text.startswith("~/"):
            return str(Path.home() / text[2:])
        if text.startswith("file://"):
            return unquote(urlparse(text).path)
        return text
