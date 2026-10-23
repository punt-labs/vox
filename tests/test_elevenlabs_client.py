"""Tests for punt_vox.providers.elevenlabs_client -- the shared, lazily built SDK."""

from __future__ import annotations

import logging
import ssl
import traceback
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest
from elevenlabs.core import ApiError, ParsingError

from punt_vox.providers.api_key import MalformedApiKeyError
from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.caller_errors import CALLER_ERRORS
from punt_vox.providers.elevenlabs_client import ElevenLabsClient
from punt_vox.providers.provider_faults import (
    ProviderConfigError,
    ProviderTransportError,
)
from punt_vox.providers.provider_refusal import ProviderRefusalError
from punt_vox.providers.provider_status import ProviderStatusError
from punt_vox.types_provider_errors import ProviderAuthError, ProviderUnavailableError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _identity(sdk: object) -> object:
    """Return the SDK itself -- builds it, through the client's one door."""
    return sdk


@pytest.fixture
def missing_ca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``SSL_CERT_FILE`` at a CA bundle that does not exist."""
    cafile = tmp_path / "gone" / "ca-bundle.pem"
    monkeypatch.setenv("SSL_CERT_FILE", str(cafile))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    return cafile


@pytest.fixture
def garbage_ca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``SSL_CERT_FILE`` at a file that exists but holds no certificate."""
    cafile = tmp_path / "ca-bundle.pem"
    cafile.write_text("this is not a PEM certificate\n")
    monkeypatch.setenv("SSL_CERT_FILE", str(cafile))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    return cafile


def _verify_failure() -> httpx.ConnectError:
    """Build the chain httpx raises when the server's certificate is untrusted."""
    error = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
    error.__cause__ = ssl.SSLCertVerificationError(1, "certificate verify failed")
    return error


# ---------------------------------------------------------------------------
# Building the SDK
# ---------------------------------------------------------------------------


class TestBuild:
    def test_names_the_variable_for_a_missing_bundle(self, missing_ca: Path) -> None:
        with pytest.raises(CaBundleError, match="SSL_CERT_FILE"):
            ElevenLabsClient().call(_identity)

    def test_message_omits_the_path(self, missing_ca: Path) -> None:
        """The message crosses the wire; the path is a host detail."""
        with pytest.raises(CaBundleError) as info:
            ElevenLabsClient().call(_identity)

        assert str(missing_ca) not in str(info.value)

    def test_message_carries_the_reason(self, missing_ca: Path) -> None:
        with pytest.raises(CaBundleError, match="No such file or directory"):
            ElevenLabsClient().call(_identity)

    def test_logs_the_path_once_at_error(
        self, missing_ca: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR), pytest.raises(CaBundleError):
            ElevenLabsClient().call(_identity)

        hits = [r for r in caplog.records if str(missing_ca) in r.getMessage()]
        assert [r.levelno for r in hits] == [logging.ERROR]

    def test_names_a_bundle_with_no_certificate(self, garbage_ca: Path) -> None:
        """An unparseable bundle is the same operator mistake as a missing one."""
        with pytest.raises(CaBundleError, match="SSL_CERT_FILE"):
            ElevenLabsClient().call(_identity)

    def test_bundle_error_chains_the_tls_cause(self, missing_ca: Path) -> None:
        with pytest.raises(CaBundleError) as info:
            ElevenLabsClient().call(_identity)

        assert isinstance(info.value.__cause__, OSError)

    def test_without_ssl_cert_file_reraises_os_error_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no SSL_CERT_FILE set, an OSError is not ours to rename."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        cause = PermissionError("denied")

        def _raise(**_kwargs: object) -> object:
            raise cause

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _raise)

        with pytest.raises(PermissionError) as info:
            ElevenLabsClient().call(_identity)

        assert info.value is cause

    def test_retries_the_build_after_a_failure(
        self, missing_ca: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed build is not cached: fixing the bundle lets the next use succeed."""
        client = ElevenLabsClient()
        with pytest.raises(CaBundleError):
            client.call(_identity)

        monkeypatch.delenv("SSL_CERT_FILE")

        assert client.call(_identity) is not None

    def test_builds_the_sdk_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built: list[object] = []

        def _fake(**_kwargs: object) -> object:
            built.append(object())
            return built[-1]

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _fake)
        client = ElevenLabsClient()

        first = client.call(_identity)
        second = client.call(_identity)

        assert first is second
        assert len(built) == 1

    def test_returns_an_injected_sdk_without_building(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _forbidden(**_kwargs: object) -> object:
            raise AssertionError

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_client.ElevenLabs", _forbidden
        )
        sdk = MagicMock()

        assert ElevenLabsClient(sdk=sdk).call(_identity) is sdk

    def test_reads_the_api_key_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key swapped into the environment after construction is not used."""
        captured: dict[str, object] = {}

        def _fake(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _fake)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "at-construction")
        client = ElevenLabsClient()
        monkeypatch.setenv("ELEVENLABS_API_KEY", "swapped-later")

        client.call(_identity)

        assert captured["api_key"] == "at-construction"

    def test_explicit_api_key_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def _fake(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _fake)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")

        ElevenLabsClient(api_key="explicit").call(_identity)

        assert captured["api_key"] == "explicit"


class TestMalformedKey:
    """A key no header may carry never reaches the SDK or the log."""

    _KEY = "SECRETKEY123"

    def test_construction_does_not_raise(self) -> None:
        """voxd builds the music client at boot; a bad key must not stop boot."""
        ElevenLabsClient(api_key=f"{self._KEY}\n")

    def test_first_call_names_the_variable_not_the_key(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        built: list[dict[str, object]] = []

        def _build(**kwargs: object) -> None:
            built.append(kwargs)

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _build)

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises(MalformedApiKeyError) as info,
        ):
            ElevenLabsClient(api_key=f"{self._KEY}\n").call(_identity)

        assert "ELEVENLABS_API_KEY" in str(info.value)
        assert self._KEY not in "".join(traceback.format_exception(info.value))
        assert self._KEY not in caplog.text
        assert built == []


class TestAbsentKey:
    """No key at construction is final: a key set later is never sent."""

    def test_first_call_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        built: list[dict[str, object]] = []

        def _build(**kwargs: object) -> None:
            built.append(kwargs)

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _build)
        client = ElevenLabsClient()
        monkeypatch.setenv("ELEVENLABS_API_KEY", "later")

        with pytest.raises(ProviderUnavailableError, match="ELEVENLABS_API_KEY"):
            client.call(_identity)

        assert built == []

    @pytest.mark.parametrize("source", ["argument", "environment"])
    def test_an_empty_key_is_no_key(
        self, source: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty key never reaches the SDK, which would send it blank."""
        monkeypatch.setenv("ELEVENLABS_API_KEY", "")
        built: list[dict[str, object]] = []

        def _build(**kwargs: object) -> None:
            built.append(kwargs)

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _build)
        client = (
            ElevenLabsClient(api_key="") if source == "argument" else ElevenLabsClient()
        )

        with pytest.raises(ProviderUnavailableError, match="ELEVENLABS_API_KEY"):
            client.call(_identity)

        assert built == []


class TestKeyCheck:
    def test_passes_with_a_key(self) -> None:
        assert ElevenLabsClient(api_key="k", sdk=MagicMock()).key_check().passed

    def test_fails_without_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

        check = ElevenLabsClient(sdk=MagicMock()).key_check()

        assert not check.passed
        assert "ELEVENLABS_API_KEY" in check.message

    def test_fails_with_a_malformed_key(self) -> None:
        check = ElevenLabsClient(api_key="k\n", sdk=MagicMock()).key_check()

        assert not check.passed
        assert "no HTTP header may carry" in check.message


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestSubscriptionCheck:
    def test_reports_the_tier(self) -> None:
        sdk = MagicMock()
        sdk.user.subscription.get.return_value = MagicMock(
            tier="creator", character_count=10, character_limit=100
        )

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert check.passed
        assert "creator" in check.message

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_key_is_a_failed_line(self, status: int) -> None:
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = ApiError(status_code=status)

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert not check.passed
        assert check.message == (
            f"ElevenLabs subscription: API key rejected (HTTP {status})"
        )

    @pytest.mark.parametrize("code", ["quota_exceeded", "missing_permissions"])
    def test_a_refusal_with_a_code_is_not_called_a_rejected_key(
        self, code: str
    ) -> None:
        """A key scoped away from the user endpoint still speaks; the line says
        the server refused, and why, not that the key is bad."""
        sdk = MagicMock()
        body = {"detail": {"status": code}}
        sdk.user.subscription.get.side_effect = ApiError(status_code=403, body=body)

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert not check.passed
        assert check.message == f"ElevenLabs subscription: refused (HTTP 403: {code})"

    def test_api_error_is_a_failed_line(self) -> None:
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = ApiError(status_code=500)

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert not check.passed
        assert "HTTP 500" in check.message

    def test_api_error_line_carries_the_server_s_message(self) -> None:
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = ApiError(
            status_code=402, body={"detail": {"message": "payment required"}}
        )

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert check.message == "ElevenLabs subscription: payment required"

    def test_a_response_off_the_schema_is_a_failed_line(self) -> None:
        """A ParsingError never escapes the health check."""
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = ParsingError(status_code=200, body={})

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert check.message == (
            "ElevenLabs subscription: response did not match the expected schema"
        )

    def test_transport_error_is_a_failed_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An httpx failure is reported, never raised out of the health check."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = httpx.ConnectError("refused")

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert not check.passed
        assert "refused" in check.message

    def test_configuration_fault_is_a_failed_line(self) -> None:
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = httpx.ProxyError("407 Proxy Auth")

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert not check.passed
        assert "ProxyError" in check.message

    def test_tls_verify_failure_names_the_variable(self, garbage_ca: Path) -> None:
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = _verify_failure()

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert not check.passed
        assert "SSL_CERT_FILE" in check.message
        assert str(garbage_ca) not in check.message

    def test_unloadable_bundle_is_a_failed_line(self, missing_ca: Path) -> None:
        check = ElevenLabsClient().subscription_check()

        assert not check.passed
        assert "SSL_CERT_FILE" in check.message


# ---------------------------------------------------------------------------
# Calling the SDK
# ---------------------------------------------------------------------------


def _raise(error: Exception) -> object:
    raise error


class TestCall:
    def test_returns_the_request_result(self) -> None:
        sdk = MagicMock()

        assert ElevenLabsClient(sdk=sdk).call(lambda s: s is sdk) is True

    def test_transport_error_is_transient_and_chained(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        cause = httpx.ConnectError("refused")

        with pytest.raises(ProviderTransportError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(cause))

        assert info.value.__cause__ is cause

    def test_tls_verify_failure_names_the_variable(self, garbage_ca: Path) -> None:
        with pytest.raises(CaBundleError, match="SSL_CERT_FILE"):
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(_verify_failure())
            )

    def test_request_error_outside_transport_is_translated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """httpx.DecodingError is a RequestError, not a TransportError; it must
        not escape raw as an unclassified exception."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        with pytest.raises(ProviderTransportError):
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(httpx.DecodingError("bad gzip"))
            )

    @pytest.mark.parametrize(
        "fault",
        [
            httpx.UnsupportedProtocol("Request URL is missing a protocol"),
            httpx.ProxyError("407 Proxy Authentication Required"),
        ],
        ids=["unsupported-protocol", "proxy-auth"],
    )
    def test_configuration_fault_is_permanent(self, fault: httpx.RequestError) -> None:
        with pytest.raises(ProviderConfigError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(fault))

        assert info.value.__cause__ is fault

    def test_header_fault_is_permanent_and_unchained(self) -> None:
        """The fault's text quotes the header -- the key -- so it is not chained."""
        fault = httpx.LocalProtocolError("Illegal header value b'key\\n'")

        with pytest.raises(ProviderConfigError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(fault))

        assert info.value.__cause__ is None

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refused_credential_is_a_refusal(self, status: int) -> None:
        """Every ElevenLabs call site -- TTS, voices, STT, music, health -- gets
        the 401 or 403 translated here, once, into the caller's error."""
        cause = ApiError(status_code=status)

        with pytest.raises(ProviderRefusalError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(cause))

        assert isinstance(info.value, CALLER_ERRORS)
        assert info.value.provider_name == "elevenlabs"
        assert info.value.status_code == status
        assert info.value.reason is None
        assert info.value.__cause__ is cause

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refusal_carries_the_server_s_code(self, status: int) -> None:
        """A refusal is not always a bad key -- a quota, unusual activity -- so
        the caller sees the code that says which."""
        body = {"detail": {"status": "quota_exceeded", "message": "m"}}

        with pytest.raises(ProviderRefusalError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(ApiError(status_code=status, body=body))
            )

        assert info.value.reason == "quota_exceeded"
        assert f"(HTTP {status}: quota_exceeded)" in str(info.value)

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refusal_logs_the_server_s_status_code(
        self, status: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ElevenLabs says why it refused a key (invalid, unusual activity); the
        log keeps that identifier."""
        body = {"detail": {"status": "detected_unusual_activity", "message": "m"}}
        cause = ApiError(status_code=status, body=body)

        with caplog.at_level(logging.ERROR), pytest.raises(ProviderRefusalError):
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(cause))

        hits = [r for r in caplog.records if "detected_unusual_activity" in r.message]
        assert [r.levelno for r in hits] == [logging.ERROR]
        assert f"HTTP {status}" in hits[0].message

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.parametrize(
        "body",
        [None, "Unauthorized", {"detail": {"status": "sk-abc 123\n"}}],
        ids=["no-body", "raw-text", "not-an-identifier"],
    )
    def test_a_refusal_with_no_code_it_can_trust_logs_only_its_status(
        self, status: int, body: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A raw-text or HTML 403 still leaves a trace, but none of its text."""
        cause = ApiError(status_code=status, body=body)

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises(ProviderRefusalError) as info,
        ):
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(cause))

        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.ERROR, f"ElevenLabs refused (HTTP {status}) with no error code")
        ]
        assert info.value.reason is None

    def test_api_error_becomes_a_provider_status_error(self) -> None:
        """The server answered: the status leaves as a vox type, so no caller
        outside this module needs the SDK's error class to interpret it."""
        cause = ApiError(status_code=429)

        with pytest.raises(ProviderStatusError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(cause))

        assert info.value.provider_name == "elevenlabs"
        assert info.value.status_code == 429
        assert info.value.detail is None
        assert info.value.__cause__ is cause

    def test_the_server_s_message_is_the_detail(self) -> None:
        body = {"detail": {"status": "bad_prompt", "message": "names an artist"}}

        with pytest.raises(ProviderStatusError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(ApiError(status_code=400, body=body))
            )

        assert info.value.detail == "names an artist"

    @pytest.mark.parametrize(
        ("status", "body", "expected"),
        [
            (404, {"detail": "voice not found"}, "voice not found"),
            (400, {"detail": {"status": "bad_prompt"}}, "bad_prompt"),
            (
                422,
                {"detail": [{"loc": ["body", "text"], "msg": "field required"}]},
                "field required",
            ),
        ],
        ids=["detail-str", "detail-status", "detail-list"],
    )
    def test_every_common_body_shape_keeps_the_server_s_reason(
        self, status: int, body: object, expected: str
    ) -> None:
        with pytest.raises(ProviderStatusError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(ApiError(status_code=status, body=body))
            )

        assert info.value.detail == expected

    def test_the_server_s_message_cannot_forge_a_log_line(self) -> None:
        """The error renders verbatim in the daemon log; the server's newline
        is escaped, and its text still names the reason."""
        body = {"detail": {"message": "names an artist\nERROR forged record\r"}}

        with pytest.raises(ProviderStatusError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(ApiError(status_code=400, body=body))
            )

        rendered = str(info.value)
        assert "\n" not in rendered
        assert "\r" not in rendered
        assert rendered.endswith("names an artist\\nERROR forged record\\r")

    @pytest.mark.parametrize(
        "body",
        [None, "plain text", {"detail": {"message": ""}}],
        ids=["none", "text", "empty-message"],
    )
    def test_a_body_without_a_message_leaves_no_detail(self, body: object) -> None:
        with pytest.raises(ProviderStatusError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(ApiError(status_code=500, body=body))
            )

        assert info.value.detail is None

    @pytest.mark.parametrize("status", [200, 204, 299])
    def test_a_success_status_that_is_not_json_is_a_failure(self, status: int) -> None:
        """The SDK raises ApiError with the raw text when a 2xx body will not
        decode as JSON; the text is never echoed, the oddity is named."""
        body = "<html>captive portal</html>"

        with pytest.raises(ProviderStatusError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(
                lambda _sdk: _raise(ApiError(status_code=status, body=body))
            )

        assert info.value.status_code == status
        assert info.value.detail == (
            f"answered HTTP {status} with a response that is not the expected JSON"
        )
        assert "captive portal" not in str(info.value)
        assert not info.value.is_permanent

    @pytest.mark.parametrize("status", [200, None], ids=["status", "no-status"])
    def test_a_response_off_the_schema_is_a_provider_status_error(
        self, status: int | None
    ) -> None:
        """The SDK raises ParsingError when a decoded body fails validation; it
        leaves as a vox type, the body unechoed, the cause chained."""
        cause = ParsingError(status_code=status, body={"secret": "body"})

        with pytest.raises(ProviderStatusError) as info:
            ElevenLabsClient(sdk=MagicMock()).call(lambda _sdk: _raise(cause))

        assert info.value.status_code == status
        assert info.value.detail == "response did not match the expected schema"
        assert "secret" not in str(info.value)
        assert info.value.__cause__ is cause


class TestStreamTo:
    def test_writes_the_stream(self, tmp_path: Path) -> None:
        target = tmp_path / "out.mp3"

        written = ElevenLabsClient(sdk=MagicMock()).stream_to(
            target, lambda _sdk: iter([b"ab", b"c"])
        )

        assert written == 3
        assert target.read_bytes() == b"abc"

    def test_transport_error_while_draining_is_translated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        def _dropping(_sdk: object) -> Iterator[bytes]:
            yield b"partial"
            raise httpx.ReadError("dropped")

        with pytest.raises(ProviderTransportError):
            ElevenLabsClient(sdk=MagicMock()).stream_to(tmp_path / "o.mp3", _dropping)

        assert list(tmp_path.iterdir()) == []

    def test_rejected_key_while_draining_is_a_provider_auth_error(
        self, tmp_path: Path
    ) -> None:
        """A lazy stream sends its request on the first pull; the 401 lands there."""

        def _rejected(_sdk: object) -> Iterator[bytes]:
            error = ApiError(status_code=401)
            if error.status_code:  # always: the branch keeps this a generator
                raise error
            yield b""  # pragma: no cover

        with pytest.raises(ProviderAuthError):
            ElevenLabsClient(sdk=MagicMock()).stream_to(tmp_path / "o.mp3", _rejected)

        assert list(tmp_path.iterdir()) == []
