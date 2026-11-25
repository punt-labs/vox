"""Environment switches that let an end-to-end test stand in for ElevenLabs.

The switches are test-only and production never sets them. The daemon
validates all three once at boot, through :meth:`CallOverrides.check` as
its playback queue is built, so a misconfigured environment stops the
daemon at start with the error naming the variable rather than running
until the first call; it then reads each switch again per use, so a test
sets them in the daemon's environment:

- ``VOX_CONVAI_BASE_URL`` replaces the REST origin. It must be exactly
  ``http://127.0.0.1:<port>`` or ``http://[::1]:<port>``; any other value
  fails the call with a :class:`ValueError` naming the variable, never a
  silent fall back to production. While it is in effect the daemon sends the
  placeholder ``test-override`` as its API key, so a listener on that port
  never receives a real credential and no ``ELEVENLABS_API_KEY`` need be
  set, and it keeps the agent registration
  (the provisioned agent and tool ids) in a sibling file named for the
  override's port, so a fake call never reads or overwrites the live
  registration and a live call never finds a fake one.
- ``VOX_CALL_ALLOW_LOOPBACK=1`` trusts plaintext ``ws://`` loopback origins for
  the conversation socket, as the in-process ``allow_loopback`` flag does.
  ``0`` and unset are off; any other value fails the call with a
  :class:`ValueError` naming the variable, so a misspelt switch is never a
  silent "Untrusted origin".
- ``VOX_PCM_OUTPUT=silent`` plays the agent's voice through
  :class:`~punt_vox.voxd.pcm_silent.SilentOutputStream` instead of PortAudio,
  so a host with no sound card still plays, cuts and plays out agent audio.
  Unset or empty is the default device; any other value fails with a
  :class:`ValueError` naming the variable, so a misspelt switch never falls
  back to PortAudio and surfaces later as an open error that does not name
  it. The playback queue reads it, because the device is chosen there.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final, final
from urllib.parse import urlsplit

from punt_vox.voxd.pcm_output import OutputStreamFactory
from punt_vox.voxd.pcm_silent import SilentOutputStream

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "LOOPBACK_HOSTS",
    "LOOPBACK_ORIGINS",
    "PLACEHOLDER_KEY",
    "CallOverrides",
]

BASE_URL_ENV: Final = "VOX_CONVAI_BASE_URL"
LOOPBACK_ENV: Final = "VOX_CALL_ALLOW_LOOPBACK"
PCM_OUTPUT_ENV: Final = "VOX_PCM_OUTPUT"
LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1"})
"""The literal loopback IPs both overrides accept; names never resolve here."""
LOOPBACK_ORIGINS: Final = frozenset(("ws", host) for host in LOOPBACK_HOSTS)
"""The plaintext socket origins ``VOX_CALL_ALLOW_LOOPBACK`` trusts."""
PLACEHOLDER_KEY: Final = "test-override"
_ORIGIN_SHAPES: Final = "http://127.0.0.1:<port> or http://[::1]:<port>"
logger = logging.getLogger(__name__)


@final
class CallOverrides:
    """Read the loopback-only test switches from the process environment."""

    __slots__ = ()

    @classmethod
    def check(cls, default: OutputStreamFactory) -> OutputStreamFactory:
        """Validate every switch now and return the output device.

        Called once at daemon boot, from the playback queue. A malformed
        switch raises the same :class:`ValueError` its use would; the REST
        and loopback values are discarded here and read again per use.
        """
        cls.rest("", "")
        cls.allow_loopback()
        return cls.pcm_output(default)

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
    def registration_path(cls, default: Path) -> Path:
        """Return the agent registration path: *default*, or its override sibling.

        With ``VOX_CONVAI_BASE_URL`` set, the registration provisioned on
        that loopback origin lives beside *default* in a file named for the
        origin's port, one per fake endpoint.
        """
        override = os.environ.get(BASE_URL_ENV)
        if override is None:
            return default
        port = urlsplit(cls._loopback_origin(override)).port
        return default.with_name(f"{default.stem}.loopback-{port}{default.suffix}")

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
        # Any userinfo, a password alone included, is more than host and port.
        extra = ("@" in url.netloc, url.path, url.query, url.fragment)
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
        value = os.environ.get(LOOPBACK_ENV, "0")
        if value not in {"0", "1"}:
            msg = f"{LOOPBACK_ENV} must be 0 or 1, not {value!r}"
            raise ValueError(msg)
        if value == "1":
            logger.warning(
                "%s=1: plaintext loopback ElevenLabs sockets are trusted", LOOPBACK_ENV
            )
        return value == "1"

    @staticmethod
    def pcm_output(default: OutputStreamFactory) -> OutputStreamFactory:
        """Return the output device the environment names, else *default*.

        Unset or empty is *default*; ``silent`` is the silent device, with one
        warning; anything else raises :class:`ValueError` naming the variable.
        """
        value = os.environ.get(PCM_OUTPUT_ENV, "")
        if value == "":
            return default
        if value != "silent":
            msg = f"{PCM_OUTPUT_ENV} must be unset or silent, not {value!r}"
            raise ValueError(msg)
        logger.warning("%s=silent: agent audio is not played", PCM_OUTPUT_ENV)
        return SilentOutputStream
