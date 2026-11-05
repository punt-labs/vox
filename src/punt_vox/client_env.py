"""Resolve the ``voxd`` endpoint overrides from the environment.

``VOXD_HOST`` / ``VOXD_PORT`` / ``VOXD_TOKEN`` let a caller point the client at a
non-default daemon (a test harness, a remote host). :class:`DaemonEnv` is the one
place that env-var policy lives, so :class:`~punt_vox.client.VoxClient` and its
sync facade read an override the same way. An invalid ``VOXD_PORT`` is refused,
never replaced by the local ``serve.port``: the operator named a daemon, and the
local file says nothing about it. Run-file resolution (``serve.port`` /
``serve.token``) lives in :mod:`punt_vox.client_endpoint`.
"""

from __future__ import annotations

import os
from typing import final

__all__ = ["DaemonEnv"]


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
        """Return ``VOXD_PORT`` as an int, or ``None`` when it is unset.

        ``None`` is the documented "no override" contract. A value that is not
        an integer in 1-65535 raises ``ValueError`` naming it.
        """
        raw = os.environ.get("VOXD_PORT", "").strip()
        if not raw:
            return None
        if not raw.isdecimal() or not 1 <= int(raw) <= 65535:
            msg = f"VOXD_PORT={raw!r} is not a port (1-65535)"
            raise ValueError(msg)
        return int(raw)

    @staticmethod
    def names_host() -> bool:
        """Return True when ``VOXD_HOST`` names the daemon's host."""
        return bool(os.environ.get("VOXD_HOST", "").strip())

    @staticmethod
    def token() -> str | None:
        """Return ``VOXD_TOKEN``, or ``None`` to fall back to the token file."""
        val = os.environ.get("VOXD_TOKEN", "").strip()
        return val if val else None
