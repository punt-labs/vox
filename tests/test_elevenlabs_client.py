"""Tests for punt_vox.providers.elevenlabs_client -- the shared, lazily built SDK."""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest
from elevenlabs.core import ApiError

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.elevenlabs_client import ElevenLabsClient

if TYPE_CHECKING:
    from pathlib import Path


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
            ElevenLabsClient().sdk()

    def test_message_omits_the_path(self, missing_ca: Path) -> None:
        """The message crosses the wire; the path is a host detail."""
        with pytest.raises(CaBundleError) as info:
            ElevenLabsClient().sdk()

        assert str(missing_ca) not in str(info.value)

    def test_message_carries_the_reason(self, missing_ca: Path) -> None:
        with pytest.raises(CaBundleError, match="No such file or directory"):
            ElevenLabsClient().sdk()

    def test_logs_the_path_once_at_error(
        self, missing_ca: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR), pytest.raises(CaBundleError):
            ElevenLabsClient().sdk()

        hits = [r for r in caplog.records if str(missing_ca) in r.getMessage()]
        assert [r.levelno for r in hits] == [logging.ERROR]

    def test_names_a_bundle_with_no_certificate(self, garbage_ca: Path) -> None:
        """An unparseable bundle is the same operator mistake as a missing one."""
        with pytest.raises(CaBundleError, match="SSL_CERT_FILE"):
            ElevenLabsClient().sdk()

    def test_bundle_error_chains_the_tls_cause(self, missing_ca: Path) -> None:
        with pytest.raises(CaBundleError) as info:
            ElevenLabsClient().sdk()

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
            ElevenLabsClient().sdk()

        assert info.value is cause

    def test_retries_the_build_after_a_failure(
        self, missing_ca: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed build is not cached: fixing the bundle lets the next use succeed."""
        client = ElevenLabsClient()
        with pytest.raises(CaBundleError):
            client.sdk()

        monkeypatch.delenv("SSL_CERT_FILE")

        assert client.sdk() is not None

    def test_builds_the_sdk_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built: list[object] = []

        def _fake(**_kwargs: object) -> object:
            built.append(object())
            return built[-1]

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _fake)
        client = ElevenLabsClient()

        first = client.sdk()
        second = client.sdk()

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

        assert ElevenLabsClient(sdk=sdk).sdk() is sdk

    def test_reads_the_api_key_at_build_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SDK's own default is read at import, before voxd loads its keys."""
        captured: dict[str, object] = {}

        def _fake(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _fake)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        client = ElevenLabsClient()
        monkeypatch.setenv("ELEVENLABS_API_KEY", "loaded-late")

        client.sdk()

        assert captured["api_key"] == "loaded-late"


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

    def test_api_error_is_a_failed_line(self) -> None:
        sdk = MagicMock()
        sdk.user.subscription.get.side_effect = ApiError(status_code=401)

        check = ElevenLabsClient(sdk=sdk).subscription_check()

        assert not check.passed
        assert "HTTP 401" in check.message

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
