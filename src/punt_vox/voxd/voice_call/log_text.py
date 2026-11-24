"""What of a call's caller and worker text may reach one line of the daemon log."""

from __future__ import annotations

import re
from typing import Final, final

__all__ = ["LogText"]

# A POSIX absolute path: a slash, then anything up to whitespace or a quote.
_ABSOLUTE_PATH: Final = re.compile(r"(?<![\w.])/[^\s'\"]+")


@final
class LogText:
    """Rewrite text so it cannot forge a log line or leak a filesystem path."""

    __slots__ = ()

    @staticmethod
    def reason(text: str) -> str:
        """Collapse line breaks to spaces and replace absolute paths by ``<path>``."""
        return _ABSOLUTE_PATH.sub("<path>", " ".join(text.splitlines()))

    @classmethod
    def failure(cls, exc: BaseException) -> str:
        """Name an exception as ``Type: message``, the message made safe."""
        return cls.reason(f"{type(exc).__name__}: {exc}").removesuffix(": ")
