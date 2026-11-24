"""Environment switches that let an end-to-end test stand in for ElevenLabs.

The switches are test-only and production never sets them. The daemon
reads them once per use, so a test sets them in the daemon's environment:

- ``VOX_CONVAI_BASE_URL`` replaces the REST origin when its host is
  ``127.0.0.1`` or ``localhost``; any other host is ignored with one warning,
  so the variable can never redirect an API key to a third party.
- ``VOX_CALL_ALLOW_LOOPBACK=1`` trusts plaintext ``ws://`` loopback origins for
  the conversation socket, as the in-process ``allow_loopback`` flag does.
- ``VOX_PCM_OUTPUT=silent`` plays the agent's voice through
  :class:`~punt_vox.voxd.pcm_silent.SilentOutputStream` instead of PortAudio,
  so a host with no sound card still plays, cuts and plays out agent audio.
  The playback queue reads it through
  :meth:`~punt_vox.voxd.pcm_silent.SilentOutputStream.from_env`, because the
  device is chosen there, not in the voice agent.
"""

from __future__ import annotations

import logging
import os
from typing import Final, final
from urllib.parse import urlsplit

__all__ = ["TestOverrides"]

BASE_URL_ENV: Final = "VOX_CONVAI_BASE_URL"
LOOPBACK_ENV: Final = "VOX_CALL_ALLOW_LOOPBACK"
_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost"})
logger = logging.getLogger(__name__)


@final
class TestOverrides:
    """Read the loopback-only test switches from the process environment."""

    __slots__ = ()

    @staticmethod
    def base_url(default: str) -> str:
        """Return the loopback REST origin from the environment, else *default*."""
        override = os.environ.get(BASE_URL_ENV, "")
        if not override:
            return default
        try:
            host = urlsplit(override).hostname
        except ValueError:
            host = None
        if host in _LOOPBACK_HOSTS:
            return override
        logger.warning("%s ignored: its host is not loopback", BASE_URL_ENV)
        return default

    @staticmethod
    def allow_loopback() -> bool:
        """Return whether plaintext loopback sockets are trusted by the environment."""
        return os.environ.get(LOOPBACK_ENV) == "1"
