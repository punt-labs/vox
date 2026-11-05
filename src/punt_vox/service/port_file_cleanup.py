"""What one attempt to clear a stale ``serve.port`` did.

A types module. ``stop`` clears the file once per run; it needs to know
whether the attempt settled the matter (removed, nothing to remove) or was
declined because a visible voxd holds the service port -- a decline leaves
the cleanup to a later tick. The clearing itself is
:class:`~punt_vox.service.stale_port_file.StalePortFile`.
"""

from __future__ import annotations

from enum import Enum
from typing import final

__all__ = ["PortFileCleanup"]


@final
class PortFileCleanup(Enum):
    """The result of clearing ``serve.port``."""

    REMOVED = "removed"
    ABSENT = "absent"
    # The file names another port: another voxd's, left alone.
    OTHER_PORT = "other_port"
    # A visible voxd holds the service port; the file may be its own.
    HELD = "held"
    # A voxd published the file after the cleanup read it: its own, left alone.
    REPLACED = "replaced"

    @property
    def settled(self) -> bool:
        """Return True when nothing is left to clear: only a decline is not."""
        return self is not PortFileCleanup.HELD
