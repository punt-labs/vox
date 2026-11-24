"""Environment switches that let an end-to-end test stand in for ElevenLabs.

The switches are test-only and production never sets them. The daemon
reads them once per use, so a test sets them in the daemon's environment:

- ``VOX_CONVAI_BASE_URL`` replaces the REST origin. It must be exactly
  ``http://127.0.0.1:<port>`` or ``http://[::1]:<port>``; any other value
  fails the call with a :class:`ValueError` naming the variable, never a
  silent fall back to production. While it is in effect the daemon sends the
  placeholder ``test-override`` as its API key, so a listener on that port
  never receives a real credential.
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

__all__ = [
    "LOOPBACK_HOSTS",
    "LOOPBACK_ORIGINS",
    "PLACEHOLDER_KEY",
    "TestOverrides",
]

BASE_URL_ENV: Final = "VOX_CONVAI_BASE_URL"
LOOPBACK_ENV: Final = "VOX_CALL_ALLOW_LOOPBACK"
LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1"})
"""The literal loopback IPs both overrides accept; names never resolve here."""
LOOPBACK_ORIGINS: Final = frozenset(("ws", host) for host in LOOPBACK_HOSTS)
"""The plaintext socket origins ``VOX_CALL_ALLOW_LOOPBACK`` trusts."""
PLACEHOLDER_KEY: Final = "test-override"
_ORIGIN_SHAPES: Final = "http://127.0.0.1:<port> or http://[::1]:<port>"
logger = logging.getLogger(__name__)


@final
class TestOverrides:
    """Read the loopback-only test switches from the process environment."""

    __slots__ = ()

    @classmethod
    def rest(cls, default: str, api_key: str) -> tuple[str, str]:
        """Return the REST origin and the key to send it.

        Unset, that is *default* and *api_key*. Set, it is the validated
        loopback origin and the placeholder key, with one warning.
        """
        override = os.environ.get(BASE_URL_ENV)
        if override is None:
            return default, api_key
        origin = cls._loopback_origin(override)
        logger.warning(
            "%s is set: ElevenLabs REST goes to loopback with the placeholder key",
            BASE_URL_ENV,
        )
        return origin, PLACEHOLDER_KEY

    @classmethod
    def _loopback_origin(cls, override: str) -> str:
        """Return *override* if it is ``http://<loopback IP>:<port>``, else raise."""
        reason = cls._rejection(override)
        if reason:
            msg = f"{BASE_URL_ENV} must be {_ORIGIN_SHAPES}: {reason}"
            raise ValueError(msg)
        return override

    @staticmethod
    def _rejection(override: str) -> str:
        """Return why *override* is not a bare loopback origin, or an empty string."""
        try:
            url = urlsplit(override)
            port = url.port
        except ValueError:
            return "it does not parse as a URL with a valid port"
        extra = (url.username, url.path, url.query, url.fragment)
        checks = (
            (url.scheme != "http", "the scheme is not http"),
            (url.hostname not in LOOPBACK_HOSTS, "the host is not a loopback IP"),
            (port is None, "the port is missing"),
            (any(extra), "it carries more than a scheme, host and port"),
        )
        return next((reason for failed, reason in checks if failed), "")

    @staticmethod
    def allow_loopback() -> bool:
        """Return whether plaintext loopback sockets are trusted by the environment."""
        return os.environ.get(LOOPBACK_ENV) == "1"
