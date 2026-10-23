"""Turns one closed audio run into a transcript, per FR-19's confidence floor.

:class:`TurnTranscriber` wraps a single :class:`~.stt_provider.STTProvider`
call with the "never fabricate on ambiguous audio" policy FR-19 requires: an
empty result, a low-confidence result, and a transient provider fault (rate
limit, 5xx, network blip) are all exactly as unactionable as each other, and
all resolve to the same ``None`` -- "ask the human to repeat" -- rather than
three different recovery paths a caller would otherwise have to know about.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import ProviderConfigError
from punt_vox.types_provider_errors import ProviderAuthError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
    from punt_vox.voxd.conversation_mode.stt_provider import (
        STTProvider,
        TranscriptEvent,
    )
    from punt_vox.voxd.conversation_mode.turn_timer import TurnTimer

__all__ = ["CONFIDENCE_FLOOR", "TurnTranscriber"]

logger = logging.getLogger(__name__)

# FR-19: a transcript below this confidence is a signal to ask the human to
# repeat, never to act on. Mirrors the floor the fake-provider tests use
# (tests/conversation_mode/test_stt_provider.py), stated once here as the
# production gate.
CONFIDENCE_FLOOR = 0.6

# The permanent STT failures transcribe re-raises, each with the detail it
# records on the turn timer first.
_PERMANENT: Final[tuple[tuple[type[ValueError], str], ...]] = (
    (ProviderAuthError, "STT auth failed"),
    (CaBundleError, "STT TLS setup failed"),
    (ProviderConfigError, "STT request misconfigured"),
)
_PERMANENT_ERRORS: Final[tuple[type[ValueError], ...]] = tuple(
    kind for kind, _ in _PERMANENT
)


@final
class TurnTranscriber:
    """Recognize one closed run of audio, applying FR-19's confidence floor."""

    __slots__ = ("_provider", "_turn_timer")
    _provider: STTProvider
    _turn_timer: TurnTimer

    def __new__(cls, provider: STTProvider, turn_timer: TurnTimer) -> Self:
        self = super().__new__(cls)
        self._provider = provider
        self._turn_timer = turn_timer
        return self

    async def transcribe(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> TranscriptEvent | None:
        """Return the final transcript event for *chunks*, or ``None`` if ambiguous.

        ``None`` covers three cases FR-19 treats identically: no events at
        all, a final event below :data:`CONFIDENCE_FLOOR`, and a transient
        provider failure -- a caller's response in every case is "ask the
        human to repeat", never to fabricate an answer from an uncertain or
        missing result.

        Raises:
            ProviderAuthError: The provider rejected its credentials.
            CaBundleError: The CA certificates in effect cannot verify the
                provider, or ``SSL_CERT_FILE`` cannot be loaded.
            ProviderConfigError: httpx refuses to send the request as the host
                configured it (proxy, base URL).
        """
        try:
            final_event = await self._final_event(self._provider.transcribe(chunks))
        except _PERMANENT_ERRORS as exc:
            # A revoked key, CA certificates that cannot verify the provider, a
            # proxy or base URL httpx refuses: each is certain and permanent --
            # every later turn hits the same failure -- the asymmetry
            # :class:`~.session_attach.BareAuthMissingError` already draws
            # against a transient session-attach fault. Folding these into the
            # broad ``except Exception`` below would launder them into "ask the
            # human to repeat", which repeats identically forever and never
            # says what is broken. Re-raised, not swallowed -- the caller
            # (CallSession) ends the call with an actionable message instead.
            detail = next(d for kind, d in _PERMANENT if isinstance(exc, kind))
            self._turn_timer.mark("stt_response_received", detail=detail)
            raise
        except Exception:
            logger.exception("STT transcribe failed mid-turn")
            self._turn_timer.mark("stt_response_received", detail="transcribe failed")
            return None
        return self._settle(final_event)

    @staticmethod
    async def _final_event(
        events: AsyncIterator[TranscriptEvent],
    ) -> TranscriptEvent | None:  # None: the provider yielded no final event
        """Drain the provider's *events* and return the last final one.

        Only a final event settles the transcript -- a provider that yields a
        partial as the last thing before ending its stream must not have that
        partial mistaken for a settled result.
        """
        final_event = None
        async for event in events:
            if event.is_final:
                final_event = event
        return final_event

    def _settle(
        self, final_event: TranscriptEvent | None
    ) -> TranscriptEvent | None:  # None: ambiguous, ask the human to repeat
        """Record what came back, then apply the confidence floor."""
        detail = (
            "no transcript"
            if final_event is None
            else f"confidence={final_event.confidence:.2f}"
        )
        self._turn_timer.mark("stt_response_received", detail=detail)
        if final_event is None or final_event.confidence < CONFIDENCE_FLOOR:
            return None
        return final_event
