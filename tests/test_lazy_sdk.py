"""Tests for punt_vox.providers.lazy_sdk -- an SDK client built on first use."""

from __future__ import annotations

import pytest

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.lazy_sdk import LazySdk
from punt_vox.providers.transport_faults import ProviderTransportError


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
