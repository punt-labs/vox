"""Test refresh serialization and client lifetime in one catalog entry."""

from __future__ import annotations

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from elevenlabs.core import ApiError

from punt_vox.providers.elevenlabs_catalog_entry import (
    _VOICE_CACHE_TTL_S,  # pyright: ignore[reportPrivateUsage]
    ElevenLabsCatalogEntry,
)
from punt_vox.providers.elevenlabs_client import ElevenLabsClient
from punt_vox.providers.elevenlabs_voices import ElevenLabsVoices
from punt_vox.types_provider_errors import ProviderAuthError


def test_catalog_does_not_keep_the_request_client() -> None:
    entry = ElevenLabsCatalogEntry()
    sdk = MagicMock()
    sdk.voices.get_all.return_value = SimpleNamespace(
        voices=[SimpleNamespace(name="Matilda", voice_id="A" * 20)]
    )
    sdk_ref = weakref.ref(sdk)
    assert entry.lookup(ElevenLabsClient(sdk=sdk), ElevenLabsVoices.list_all) == [
        "matilda"
    ]
    del sdk
    gc.collect()

    assert sdk_ref() is None
    assert entry.lookup(ElevenLabsClient(), ElevenLabsVoices.list_all) == ["matilda"]


def test_refresh_uses_current_client(mock_elevenlabs_client: MagicMock) -> None:
    refreshed = MagicMock()
    refreshed.voices.get_all.return_value = SimpleNamespace(
        voices=[SimpleNamespace(name="New Name", voice_id="A" * 20)]
    )
    entry = ElevenLabsCatalogEntry()
    with patch("punt_vox.providers.voice_resolver.time") as clock:
        clock.monotonic.return_value = 100.0
        names = entry.lookup(
            ElevenLabsClient(sdk=mock_elevenlabs_client), ElevenLabsVoices.list_all
        )
        assert "matilda" in names
        clock.monotonic.return_value += _VOICE_CACHE_TTL_S
        assert entry.lookup(
            ElevenLabsClient(sdk=refreshed), ElevenLabsVoices.list_all
        ) == ["new name"]

    mock_elevenlabs_client.voices.get_all.assert_called_once()
    refreshed.voices.get_all.assert_called_once()


def test_concurrent_lookups_share_the_first_fetch(
    mock_elevenlabs_client: MagicMock,
) -> None:
    get_all = mock_elevenlabs_client.voices.get_all
    entering = MagicMock()
    lock = Lock()
    fetch_started, second_lookup = Event(), Event()

    def acquire() -> None:
        if fetch_started.is_set():
            second_lookup.set()
        lock.acquire()

    def fetch() -> object:
        fetch_started.set()
        assert second_lookup.wait(timeout=5)
        return get_all.return_value

    def release(*_args: object) -> None:
        lock.release()

    entering.__enter__.side_effect = acquire
    entering.__exit__.side_effect = release
    get_all.side_effect = fetch
    with patch(
        "punt_vox.providers.elevenlabs_catalog_entry.Lock", return_value=entering
    ):
        entry = ElevenLabsCatalogEntry()
    client = ElevenLabsClient(sdk=mock_elevenlabs_client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(entry.lookup, client, ElevenLabsVoices.list_all)
        assert fetch_started.wait(timeout=5)
        second = pool.submit(entry.lookup, client, ElevenLabsVoices.list_all)
        assert first.result(timeout=5) == second.result(timeout=5)

    get_all.assert_called_once()


def test_failed_fetch_does_not_poison_entry(mock_elevenlabs_client: MagicMock) -> None:
    refused = MagicMock()
    refused.voices.get_all.side_effect = ApiError(status_code=401)
    entry = ElevenLabsCatalogEntry()
    with pytest.raises(ProviderAuthError):
        entry.lookup(ElevenLabsClient(sdk=refused), ElevenLabsVoices.list_all)

    assert "matilda" in entry.lookup(
        ElevenLabsClient(sdk=mock_elevenlabs_client), ElevenLabsVoices.list_all
    )
