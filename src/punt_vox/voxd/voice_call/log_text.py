"""What of a call's caller and worker text may reach one line of the daemon log."""

from __future__ import annotations

import re
from typing import Final, final

from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.inputs import MicInput

__all__ = ["LogText"]

# A POSIX absolute path: a slash, then anything up to whitespace or a quote.
_ABSOLUTE_PATH: Final = re.compile(r"(?<![\w.])/[^\s'\"]+")
# Every character str.splitlines() treats as a line boundary.
_LINE_BREAKS: Final = re.compile(r"[\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]+")
_ECHO_CONTROL: Final = MicInput.ECHO_CONTROL | {"none"}
_TOOLS: Final = frozenset(tool.name for tool in BossTools.CATALOG)


@final
class LogText:
    """Rewrite text so it cannot forge a log line or leak a filesystem path."""

    __slots__ = ()

    @staticmethod
    def reason(text: str) -> str:
        """Collapse line breaks to spaces and replace absolute paths by ``<path>``."""
        return _ABSOLUTE_PATH.sub("<path>", _LINE_BREAKS.sub(" ", text))

    @staticmethod
    def echo_control(value: object) -> str:
        """Return a request's echo declaration when it is one a call accepts."""
        return value if isinstance(value, str) and value in _ECHO_CONTROL else "other"

    @staticmethod
    def tool_name(name: str) -> str:
        """Return an agent-chosen tool name only when the catalog has it."""
        return name if name in _TOOLS else "unknown"
