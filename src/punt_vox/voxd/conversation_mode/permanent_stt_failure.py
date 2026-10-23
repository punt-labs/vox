"""Which speech-recognition failures end a call, and what the call says about each.

:class:`~.turn_transcriber.TurnTranscriber` re-raises the failures
:meth:`PermanentSttFailure.ends_call` names, marking the turn timer with the
failure's :attr:`~PermanentSttFailure.detail`; ``CallSession`` catches them as
:data:`CALL_ENDING_ERRORS` and speaks the :attr:`~PermanentSttFailure.ending`
before it ends the call.
"""

from __future__ import annotations

from typing import Final, Self, final

from punt_vox.providers.api_key import MalformedApiKeyError
from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import PERMANENT_ERRORS, ProviderConfigError
from punt_vox.providers.provider_refusal import ProviderRefusalError
from punt_vox.providers.provider_status import ProviderStatusError
from punt_vox.types_provider_errors import ProviderAuthError, ProviderUnavailableError

__all__ = ["CALL_ENDING_ERRORS", "PermanentSttFailure"]


@final
class PermanentSttFailure:
    """What a call records and says when recognition fails for good.

    Which failures are permanent is ``PERMANENT_ERRORS``'s call, and an
    answered failure's own (``ProviderStatusError.is_permanent``); this class
    only words each one -- the turn-timer detail ``TurnTranscriber`` marks, and
    the sentence ``CallSession`` speaks before it ends the call. One row per
    kind keeps the two from drifting apart.
    """

    __slots__ = ("_detail", "_ending", "_kind")
    _kind: type[Exception]
    _detail: str
    _ending: str

    def __new__(cls, kind: type[Exception], detail: str, ending: str) -> Self:
        self = super().__new__(cls)
        self._kind = kind
        self._detail = detail
        self._ending = ending
        return self

    @staticmethod
    def ends_call(exc: Exception) -> bool:
        """Return whether *exc* is certain and permanent: every later turn fails so.

        A revoked or missing key, CA certificates that cannot verify the
        provider, a proxy or base URL httpx refuses, a refusal of the request
        itself (a permanent ``ProviderStatusError``): the asymmetry
        ``BareAuthMissingError`` already draws against a transient
        session-attach fault. Folding these into "ask the human to repeat"
        would repeat identically forever and never say what is broken. A rate
        limit or a 5xx may clear by the next turn.
        """
        if isinstance(exc, ProviderStatusError):
            return exc.is_permanent
        return isinstance(exc, PERMANENT_ERRORS)

    @classmethod
    def of(cls, exc: Exception) -> PermanentSttFailure:
        """Return the wording for *exc*: its own row, or the generic one.

        An answered failure is worded with its status, and a refusal that names
        its code with that code, so neither has a fixed row: a 403 for an
        exhausted quota or a flagged account is not a bad key, and saying
        "credentials were rejected" would send the caller to fix the wrong
        thing. A refusal with no code falls to ``ProviderAuthError``'s row.
        Otherwise first match wins, so a subclass's row precedes its base's.
        """
        match exc:
            case ProviderStatusError(status_code=status):
                return cls._refused(f"HTTP {status}")
            case ProviderRefusalError(reason=str(), answer=answer):
                return cls._refused(answer)
            case _:
                return cls._row_for(exc)

    @classmethod
    def _refused(cls, answer: str) -> PermanentSttFailure:
        """Return the wording for a refusal no retry changes, naming *answer*.

        *answer* is the status, and the server's code when it is a plain
        identifier. The server's own message stays in vox.log: it is the
        provider's text, not a sentence to speak to the caller.
        """
        return cls(
            # Only the fixed rows are looked up by kind; this one never is.
            Exception,
            f"STT refused ({answer})",
            f"Sorry, this call can't continue -- speech recognition was refused "
            f"({answer}); vox.log has the server's reason. Ending the call now.",
        )

    @staticmethod
    def _row_for(exc: Exception) -> PermanentSttFailure:
        """Return the first fixed row whose kind *exc* is, or the generic one."""
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

# What TurnTranscriber.transcribe re-raises for its caller to end the call on:
# every PERMANENT_ERRORS member, and a ProviderStatusError only when
# is_permanent -- a transient one resolves to "ask to repeat" inside it.
CALL_ENDING_ERRORS: Final[tuple[type[Exception], ...]] = (
    *PERMANENT_ERRORS,
    ProviderStatusError,
)
