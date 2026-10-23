"""Tests for :class:`~.permanent_stt_failure.PermanentSttFailure`."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from punt_vox.providers.api_key import MalformedApiKeyError
from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.provider_faults import (
    PERMANENT_ERRORS,
    ProviderConfigError,
    ProviderTransportError,
)
from punt_vox.providers.provider_status import ProviderStatusError
from punt_vox.types_provider_errors import ProviderAuthError, ProviderUnavailableError
from punt_vox.voxd.conversation_mode.permanent_stt_failure import (
    CALL_ENDING_ERRORS,
    PermanentSttFailure,
)

# Every member of the one permanent set, built the way its raiser builds it.
_PERMANENT_FACTORIES: dict[type[ValueError], Callable[[], ValueError]] = {
    ProviderAuthError: lambda: ProviderAuthError("elevenlabs", 401),
    ProviderUnavailableError: lambda: ProviderUnavailableError(
        "elevenlabs", "voxd has no ELEVENLABS_API_KEY"
    ),
    CaBundleError: lambda: CaBundleError("SSL_CERT_FILE cannot be loaded"),
    ProviderConfigError: lambda: ProviderConfigError("proxy refused"),
}


def test_every_permanent_error_is_exercised_here() -> None:
    """A member added to PERMANENT_ERRORS fails here until it gets a row."""
    assert set(_PERMANENT_FACTORIES) == set(PERMANENT_ERRORS)


def test_the_call_ending_set_is_the_permanent_set_and_answered_failures() -> None:
    assert set(CALL_ENDING_ERRORS) == {*PERMANENT_ERRORS, ProviderStatusError}


class TestEndsCall:
    """Certain and permanent ends the call; anything that may clear does not."""

    @pytest.mark.parametrize("kind", PERMANENT_ERRORS, ids=lambda kind: kind.__name__)
    def test_every_permanent_error_ends_the_call(self, kind: type[ValueError]) -> None:
        assert PermanentSttFailure.ends_call(_PERMANENT_FACTORIES[kind]())

    def test_a_malformed_key_ends_the_call(self) -> None:
        """A subclass of a permanent kind is permanent too."""
        assert PermanentSttFailure.ends_call(MalformedApiKeyError("ELEVENLABS ..."))

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_a_permanent_status_ends_the_call(self, status: int) -> None:
        error = ProviderStatusError("elevenlabs", status, None)

        assert PermanentSttFailure.ends_call(error)

    @pytest.mark.parametrize("status", [200, 429, 500, 502, 503, None])
    def test_a_transient_status_does_not(self, status: int | None) -> None:
        error = ProviderStatusError("elevenlabs", status, None)

        assert not PermanentSttFailure.ends_call(error)

    @pytest.mark.parametrize(
        "error",
        [
            ProviderTransportError("dropped"),
            TimeoutError("slow"),
            RuntimeError("bug"),
            ValueError("not a provider fault"),
        ],
        ids=["transport", "timeout", "runtime", "plain-value-error"],
    )
    def test_any_other_failure_does_not(self, error: Exception) -> None:
        assert not PermanentSttFailure.ends_call(error)


class TestWording:
    """One row per permanent kind: the timer detail and the spoken ending."""

    _GENERIC = PermanentSttFailure.of(ValueError("not a provider fault"))

    @pytest.mark.parametrize("kind", PERMANENT_ERRORS, ids=lambda kind: kind.__name__)
    def test_every_permanent_kind_has_its_own_wording(
        self, kind: type[ValueError]
    ) -> None:
        wording = PermanentSttFailure.of(_PERMANENT_FACTORIES[kind]())

        assert wording.detail != self._GENERIC.detail
        assert wording.ending != self._GENERIC.ending
        assert wording.ending.endswith("Ending the call now.")

    def test_a_refusal_is_worded_with_its_status(self) -> None:
        wording = PermanentSttFailure.of(ProviderStatusError("elevenlabs", 403, "x"))

        assert wording.detail == "STT refused (HTTP 403)"
        assert wording.ending == (
            "Sorry, this call can't continue -- speech recognition was refused "
            "(HTTP 403); vox.log has the server's reason. Ending the call now."
        )

    def test_a_refusal_never_speaks_the_server_s_text(self) -> None:
        """The server's message is for vox.log, not the caller's ear."""
        wording = PermanentSttFailure.of(
            ProviderStatusError("elevenlabs", 422, "secret server text")
        )

        assert "secret server text" not in wording.ending

    def test_a_malformed_key_is_worded_apart_from_its_base(self) -> None:
        """The subclass's row precedes ProviderConfigError's, so it is reached."""
        key = PermanentSttFailure.of(MalformedApiKeyError("OPENAI_API_KEY ..."))
        config = PermanentSttFailure.of(ProviderConfigError("proxy refused"))

        assert key.detail == "STT API key unsendable"
        assert "API key" in key.ending
        assert key.ending != config.ending
