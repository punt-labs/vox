"""What one ``terminate()`` of a pinned voxd process came to.

A types module. The handle that sends the signals is the only party that knows
which signals went out and whether the kernel refused them, so it reports that
here, and ``stop``'s report says exactly what happened to each process.
"""

from __future__ import annotations

from enum import Enum
from typing import final

__all__ = ["Termination"]


@final
class Termination(Enum):
    """The outcome of one ``ProcessHandle.terminate()``."""

    EXITED = "exited"
    # SIGTERM, then SIGKILL after the grace; the process is still running.
    SURVIVED = "survived"
    # The budget was below the grace floor, so only SIGTERM was sent; the
    # process had not exited when the budget ran out.
    TERM_ONLY = "term_only"
    # The kernel refused the signal (permission denied); nothing was delivered.
    REFUSED = "refused"
