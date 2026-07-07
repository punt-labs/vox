"""Resolve the ``voxd`` endpoint overrides from the environment.

``VOXD_HOST`` / ``VOXD_PORT`` / ``VOXD_TOKEN`` let a caller point the client at a
non-default daemon (a test harness, a remote host). :class:`DaemonEnv` is the one
place that env-var policy lives, so :class:`~punt_vox.client.VoxClient` and its
sync facade read an override the same way and an invalid port is logged and
ignored rather than crashing a connect. Run-file resolution (``serve.port`` /
``serve.token``) stays in :mod:`punt_vox.client`, next to the connection logic
that falls back to it.
"""

from __future__ import annotations

import logging
import os
from typing import final

__all__ = ["DaemonEnv"]

logger = logging.getLogger(__name__)


@final
class DaemonEnv:
    """The ``VOXD_*`` endpoint-override resolvers, grouped as one policy.

    Stateless: each resolver reads the process environment fresh, so a test that
    sets a variable sees it on the next call. The methods are static because the
    policy is the environment, not per-instance data.
    """

    __slots__ = ()

    @staticmethod
    def host() -> str:
        """Return ``VOXD_HOST`` or the loopback default."""
        val = os.environ.get("VOXD_HOST", "").strip()
        return val if val else "127.0.0.1"

    @staticmethod
    def port() -> int | None:
        """Return ``VOXD_PORT`` as an int, or ``None`` to fall back to the file.

        ``None`` is the documented "no override" contract, not a parse failure:
        an unset var, a non-integer, or an out-of-range port all mean "use the
        file", and the two malformed cases are logged so the fallback is not
        silent.
        """
        raw = os.environ.get("VOXD_PORT", "").strip()
        if not raw:
            return None
        try:
            port = int(raw)
        except ValueError:
            logger.warning("VOXD_PORT=%r is not an integer, ignoring", raw)
            return None
        if not 1 <= port <= 65535:
            logger.warning("VOXD_PORT=%d is out of range (1-65535), ignoring", port)
            return None
        return port

    @staticmethod
    def token() -> str | None:
        """Return ``VOXD_TOKEN``, or ``None`` to fall back to the token file."""
        val = os.environ.get("VOXD_TOKEN", "").strip()
        return val if val else None
