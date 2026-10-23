"""Tests for :class:`~punt_vox.providers.elevenlabs_answer.ElevenLabsAnswer`."""

from __future__ import annotations

import logging

import pytest

from punt_vox.providers.elevenlabs_answer import ElevenLabsAnswer
from punt_vox.providers.provider_refusal import ProviderRefusalError
from punt_vox.providers.provider_status import ProviderStatusError


class TestError:
    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refused_status_is_a_refusal(self, status: int) -> None:
        error = ElevenLabsAnswer(status, None).error()

        assert isinstance(error, ProviderRefusalError)
        assert error.status_code == status

    def test_a_refusal_keeps_the_server_s_code(self) -> None:
        body = {"detail": {"status": "quota_exceeded", "message": "m"}}

        error = ElevenLabsAnswer(403, body).error()

        assert isinstance(error, ProviderRefusalError)
        assert error.answer == "HTTP 403: quota_exceeded"

    def test_a_refusal_logs_its_code(self, caplog: pytest.LogCaptureFixture) -> None:
        body = {"detail": {"status": "detected_unusual_activity"}}

        with caplog.at_level(logging.ERROR):
            ElevenLabsAnswer(401, body).error()

        assert [r.getMessage() for r in caplog.records] == [
            "ElevenLabs refused (HTTP 401: detected_unusual_activity)"
        ]

    def test_a_refusal_without_a_code_logs_its_status_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A proxy's HTML page leaves a trace, and none of its text."""
        with caplog.at_level(logging.DEBUG):
            ElevenLabsAnswer(403, "<html>Forbidden by proxy</html>").error()

        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.ERROR, "ElevenLabs refused (HTTP 403) with no error code")
        ]

    def test_any_other_status_carries_the_server_s_message(self) -> None:
        body = {"detail": {"message": "too many requests"}}

        error = ElevenLabsAnswer(429, body).error()

        assert isinstance(error, ProviderStatusError)
        assert (error.status_code, error.detail) == (429, "too many requests")

    @pytest.mark.parametrize("status", [200, 299])
    def test_a_success_status_is_a_body_that_did_not_decode(self, status: int) -> None:
        """The raw text of an undecodable success is never echoed."""
        error = ElevenLabsAnswer(status, "<html>secret</html>").error()

        assert isinstance(error, ProviderStatusError)
        assert error.detail == (
            f"answered HTTP {status} with a response that is not the expected JSON"
        )

    def test_no_status_is_a_status_error_without_one(self) -> None:
        error = ElevenLabsAnswer(None, None).error()

        assert isinstance(error, ProviderStatusError)
        assert (error.status_code, error.detail) == (None, None)


class TestOffSchema:
    def test_the_body_is_not_echoed(self) -> None:
        error = ElevenLabsAnswer(200, {"detail": "secret"}).off_schema()

        assert (error.status_code, error.detail) == (
            200,
            "response did not match the expected schema",
        )
