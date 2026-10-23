"""Tests for punt_vox.providers.lazy_sdk -- an SDK client built on first use."""

from __future__ import annotations

import logging
import traceback

import httpx
import openai
import pytest
from elevenlabs.client import ElevenLabs

from punt_vox.providers.api_key import MalformedApiKeyError
from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.lazy_sdk import LazySdk
from punt_vox.providers.provider_faults import (
    ProviderConfigError,
    ProviderTransportError,
    UnsendableHeaderError,
)
from punt_vox.types_provider_errors import ProviderUnavailableError


class _TransportError(Exception):
    """Stands in for an SDK's transport error type."""


class _Counter:
    """A factory that records how often it ran."""

    __slots__ = ("_calls",)
    _calls: int

    def __new__(cls) -> _Counter:
        self = super().__new__(cls)
        self._calls = 0
        return self

    @property
    def calls(self) -> int:
        return self._calls

    def __call__(self) -> object:
        self._calls += 1
        return object()


def test_construction_does_not_build() -> None:
    factory = _Counter()

    LazySdk(factory, transport=_TransportError)

    assert factory.calls == 0


def test_first_get_builds_once() -> None:
    factory = _Counter()
    sdk = LazySdk(factory, transport=_TransportError)

    first = sdk.get()

    assert sdk.get() is first
    assert factory.calls == 1


def test_injected_sdk_is_returned_without_building() -> None:
    factory = _Counter()
    injected = object()

    assert LazySdk(factory, transport=_TransportError, sdk=injected).get() is injected
    assert factory.calls == 0


def _unloadable() -> object:
    raise FileNotFoundError(2, "No such file or directory", "/gone/ca.pem")


def test_os_error_with_bundle_set_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/gone/ca.pem")

    with pytest.raises(CaBundleError, match="SSL_CERT_FILE") as info:
        LazySdk(_unloadable, transport=_TransportError).get()

    assert isinstance(info.value.__cause__, FileNotFoundError)


def test_os_error_without_bundle_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    with pytest.raises(FileNotFoundError):
        LazySdk(_unloadable, transport=_TransportError).get()


def test_failed_build_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/gone/ca.pem")
    outcomes: list[object] = []

    def _flaky() -> object:
        if not outcomes:
            outcomes.append(None)
            return _unloadable()
        return object()

    sdk = LazySdk(_flaky, transport=_TransportError)
    with pytest.raises(CaBundleError):
        sdk.get()

    assert sdk.get() is not None


def _raise(error: Exception) -> object:
    raise error


def test_call_returns_the_request_result() -> None:
    injected = object()
    sdk = LazySdk(_Counter(), transport=_TransportError, sdk=injected)

    assert sdk.call(lambda client: client is injected) is True


def test_call_translates_the_transport_error_and_chains_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    cause = _TransportError("refused")
    sdk = LazySdk(_Counter(), transport=_TransportError)

    with pytest.raises(ProviderTransportError) as info:
        sdk.call(lambda _client: _raise(cause))

    assert info.value.__cause__ is cause


def test_call_passes_other_errors_through() -> None:
    sdk = LazySdk(_Counter(), transport=_TransportError)

    with pytest.raises(KeyError):
        sdk.call(lambda _client: _raise(KeyError("server said no")))


class TestHeaderFaultChain:
    """httpx's header fault quotes the header value: the chain is cut, not kept."""

    _KEY = "SECRETKEY123"

    def _call(self) -> UnsendableHeaderError:
        fault = httpx.LocalProtocolError(f"Illegal header value b'{self._KEY}\\n'")
        sdk = LazySdk(_Counter(), transport=httpx.RequestError)
        with pytest.raises(UnsendableHeaderError) as info:
            sdk.call(lambda _client: _raise(fault))
        return info.value

    def test_cause_is_cut(self) -> None:
        error = self._call()

        assert error.__cause__ is None
        assert error.__suppress_context__

    def test_traceback_never_quotes_the_key(self) -> None:
        text = "".join(traceback.format_exception(self._call()))

        assert self._KEY not in text

    def test_logged_traceback_never_quotes_the_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """What a handler's ``logger.exception`` writes to vox.log."""
        log = logging.getLogger("test_lazy_sdk")
        with caplog.at_level(logging.DEBUG):
            try:
                raise self._call()
            except UnsendableHeaderError:
                log.exception("request failed")

        assert self._KEY not in caplog.text


class TestUnbuildableClient:
    """A constructor that rejects the host's proxy or base URL is configuration."""

    @staticmethod
    def _real_sdks() -> tuple[LazySdk[object], ...]:
        return (
            LazySdk(lambda: ElevenLabs(api_key="k"), transport=httpx.RequestError),
            LazySdk(
                lambda: openai.OpenAI(api_key="k"), transport=openai.APIConnectionError
            ),
        )

    @pytest.fixture
    def clean_proxy_env(self, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
        for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        return monkeypatch

    def test_unknown_proxy_scheme_is_a_config_error(
        self, clean_proxy_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Both real SDKs refuse ``socks9://`` at construction with a ValueError."""
        clean_proxy_env.setenv("HTTPS_PROXY", "socks9://user:s3cret@proxyhost:1")

        for sdk in self._real_sdks():
            with (
                caplog.at_level(logging.ERROR),
                pytest.raises(ProviderConfigError) as info,
            ):
                sdk.get()

            assert "HTTPS_PROXY" in str(info.value)
            assert "proxyhost" not in str(info.value)
            assert isinstance(info.value.__cause__, ValueError)
        assert "socks9" in caplog.text
        assert "s3cret" not in caplog.text

    def test_socks_proxy_without_socks_support_is_a_config_error(
        self, clean_proxy_env: pytest.MonkeyPatch
    ) -> None:
        """httpx raises ImportError for a SOCKS proxy when socksio is absent."""
        clean_proxy_env.setenv("ALL_PROXY", "socks5://proxyhost:1080")

        def _no_socksio() -> object:
            raise ImportError(
                "Using SOCKS proxy, but the 'socksio' package is not installed."
            )

        with pytest.raises(ProviderConfigError, match="SOCKS"):
            LazySdk(_no_socksio, transport=_TransportError).get()

    def test_other_import_error_propagates(self) -> None:
        def _broken() -> object:
            raise ImportError("No module named 'h2'")

        with pytest.raises(ImportError):
            LazySdk(_broken, transport=_TransportError).get()

    @pytest.mark.parametrize(
        "error",
        [
            MalformedApiKeyError("OPENAI_API_KEY contains a character ..."),
            ProviderUnavailableError("openai", "voxd has no OPENAI_API_KEY"),
        ],
        ids=["malformed-key", "absent-key"],
    )
    def test_a_named_fault_from_the_factory_passes_unchanged(
        self, error: ValueError
    ) -> None:
        """A factory that already named the fault (the key) is not re-worded as a
        proxy or base-URL problem."""

        def _refuse() -> object:
            raise error

        with pytest.raises(type(error)) as info:
            LazySdk(_refuse, transport=_TransportError).get()

        assert info.value is error
