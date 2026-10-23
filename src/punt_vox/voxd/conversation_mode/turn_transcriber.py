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

from punt_vox.providers.api_key import MalformedApiKeyError
from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import PERMANENT_ERRORS, ProviderConfigError
from punt_vox.types_provider_errors import ProviderAuthError, ProviderUnavailableError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
    from punt_vox.voxd.conversation_mode.stt_provider import (
        STTProvider,
        TranscriptEvent,
    )
    from punt_vox.voxd.conversation_mode.turn_timer import TurnTimer

__all__ = ["CONFIDENCE_FLOOR", "PermanentSttFailure", "TurnTranscriber"]

logger = logging.getLogger(__name__)

# FR-19: a transcript below this confidence is a signal to ask the human to
# repeat, never to act on. Mirrors the floor the fake-provider tests use
# (tests/conversation_mode/test_stt_provider.py), stated once here as the
# production gate.
CONFIDENCE_FLOOR = 0.6


@final
class PermanentSttFailure:
    """What a call records and says when recognition fails for good.

    Which failures are permanent is ``PERMANENT_ERRORS``'s call; this class
    only words each one -- the turn-timer detail :class:`TurnTranscriber`
    marks, and the sentence ``CallSession`` speaks before it ends the call.
    One row per kind keeps the two from drifting apart.
    """

    __slots__ = ("_detail", "_ending", "_kind")
    _kind: type[ValueError]
    _detail: str
    _ending: str

    def __new__(cls, kind: type[ValueError], detail: str, ending: str) -> Self:
        self = super().__new__(cls)
        self._kind = kind
        self._detail = detail
        self._ending = ending
        return self

    @classmethod
    def of(cls, exc: ValueError) -> PermanentSttFailure:
        """Return the wording for *exc*: its own row, or the generic one.

        First match wins, so a subclass's row precedes its base's.
        """
        return next((row for row in _KNOWN if isinstance(exc, row._kind)), _UNKNOWN)

    @property
    def detail(self) -> str:
        """Return the turn-timer detail for this failure."""
        return self._detail

    @property
    def ending(self) -> str:
        """Return what the call says before it ends on this failure."""
        return self._ending


_KNOWN: Final = (
    # Certain and permanent, unlike "ask to repeat" -- mirrors reply_recovery's
    # _BARE_AUTH_MISSING sentence.
    PermanentSttFailure(
        ProviderAuthError,
        "STT auth failed",
        "Sorry, this call can't continue -- the speech-recognition credentials "
        "were rejected. Ending the call now.",
    ),
    # No key was set when the provider was built, so every turn finds none; the
    # logged ProviderUnavailableError names the variable voxd reads.
    PermanentSttFailure(
        ProviderUnavailableError,
        "STT API key missing",
        "Sorry, this call can't continue -- speech recognition has no API key "
        "to send. Set the key and restart voxd; vox.log names the variable. "
        "Ending the call now.",
    ),
    # The trust anchors (SSL_CERT_FILE, SSL_CERT_DIR, or the client's default)
    # distrust the issuer, or the certificate names another host or has
    # expired. vox.log says which, and what to change.
    PermanentSttFailure(
        CaBundleError,
        "STT TLS setup failed",
        "Sorry, this call can't continue -- speech recognition can't verify the "
        "provider's TLS certificate. Check SSL_CERT_FILE and the provider "
        "address; vox.log has the details. Ending the call now.",
    ),
    # A pasted key with a space, a newline, or a non-ASCII character (a smart
    # quote, a non-breaking space) can never be sent; the log names the variable.
    PermanentSttFailure(
        MalformedApiKeyError,
        "STT API key unsendable",
        "Sorry, this call can't continue -- the speech recognition API key holds "
        "a character no request can carry: a space, a newline, or a pasted "
        "non-ASCII character. Re-enter the key; vox.log names the variable. "
        "Ending the call now.",
    ),
    # A proxy or base URL httpx will not use is host configuration too.
    PermanentSttFailure(
        ProviderConfigError,
        "STT request misconfigured",
        "Sorry, this call can't continue -- speech recognition can't send its "
        "request as this host is configured. Check the proxy settings and the "
        "API key; vox.log has the details. Ending the call now.",
    ),
)

# A PERMANENT_ERRORS member with no row above is still an ending.
_UNKNOWN: Final = PermanentSttFailure(
    ValueError,
    "STT failed permanently",
    "Sorry, this call can't continue -- speech recognition failed in a way a "
    "retry won't fix; vox.log has the details. Ending the call now.",
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
            ProviderUnavailableError: The provider has no API key to send.
            CaBundleError: The CA certificates in effect cannot verify the
                provider, or ``SSL_CERT_FILE`` cannot be loaded.
            ProviderConfigError: httpx refuses to send the request as the host
                configured it (proxy, base URL), or the API key holds a
                character no header may carry.
        """
        try:
            final_event = await self._final_event(self._provider.transcribe(chunks))
        except PERMANENT_ERRORS as exc:
            # A revoked or missing key, CA certificates that cannot verify the
            # provider, a proxy or base URL httpx refuses: each is certain and
            # permanent -- every later turn hits the same failure -- the
            # asymmetry :class:`~.session_attach.BareAuthMissingError` already
            # draws against a transient session-attach fault. Folding these into
            # the broad ``except Exception`` below would launder them into "ask
            # the human to repeat", which repeats identically forever and never
            # says what is broken. Re-raised, not swallowed -- the caller
            # (CallSession) ends the call with an actionable message instead.
            self._turn_timer.mark(
                "stt_response_received", detail=PermanentSttFailure.of(exc).detail
            )
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
