"""The shared log line format and the client-process role literal.

Extracted so both logging configs and the append handler that renders records
read one source of truth for the format without importing the config or handler
stack -- importable with zero heavy dependencies (PY-IC-9).
"""

from __future__ import annotations

from typing import Literal

__all__ = ["LOG_DATE_FORMAT", "LOG_FORMAT", "Role"]

# Which client process a line came from. A client stamps ``client.<role>.`` onto
# its logger name so its lines grep apart from the daemon's own in one vox.log.
Role = Literal["hook", "mcp", "cli", "playback"]

# The one line format every writer renders, so daemon and client lines read alike.
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
