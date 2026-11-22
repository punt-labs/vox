"""Voxd speech boundary for the Claude Code hook handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.session_spec import SessionSpec
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.types_synthesis_errors import (
    ModelNotAvailableError,
    ProviderNotConfiguredError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from punt_vox.client_sync import VoxClientSync
    from punt_vox.config import VoxConfig


class HookSpeech:
    """Translate configured hook speech into one daemon request."""

    __slots__ = ("_client", "_logger")
    _client: Callable[[], VoxClientSync]
    _logger: Logger

    def __new__(cls, client: Callable[[], VoxClientSync], logger: Logger) -> Self:
        """Bind the client factory and hook log sink."""
        self = super().__new__(cls)
        self._client = client
        self._logger = logger
        return self

    def speak(self, text: str, config: VoxConfig) -> None:
        """Synthesize and play a phrase via voxd.

        Builds the wire spec through :class:`SessionSpec` so state -- not a
        daemon-side probe -- is the sole authority on which provider voxd runs.
        A hook has no caller to answer, so a client-side refusal
        (:class:`ProviderNotConfiguredError` for empty provider state,
        :class:`ModelNotAvailableError` for a hand-edited provider/model pair)
        is logged as a WARNING in ``vox.log`` and no audio plays -- the F1
        contract in ``docs/provider-authority.md`` §4. ``VoxdConnectionError``
        and ``VoxdProtocolError`` are still caught so a missing daemon never
        crashes a hook either.
        """
        try:
            spec = SessionSpec(config).fill(SynthesisSpec(rate=90))
        except ProviderNotConfiguredError:
            self._logger.warning(
                "hook speech skipped: no TTS provider configured for this repo"
            )
            return
        except ModelNotAvailableError as exc:
            self._logger.warning("hook speech skipped: %s", exc)
            return
        try:
            self._client().synthesize(text, spec)
        except (VoxdConnectionError, VoxdProtocolError):
            self._logger.warning("voxd not running, skipping speech")
