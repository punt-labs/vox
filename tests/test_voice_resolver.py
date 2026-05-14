"""Tests for punt_vox.providers.voice_resolver."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from punt_vox.providers.voice_resolver import VoiceResolver
from punt_vox.types import VoiceNotFoundError


def _make_loader(voices: dict[str, str]) -> MagicMock:
    """Create a mock loader that returns the given voice dict."""
    return MagicMock(return_value=voices)


class TestVoiceResolverResolve:
    def test_resolve_loads_on_first_call(self) -> None:
        """Loader is called on first resolve, value returned."""
        loader = _make_loader({"alice": "id-alice"})
        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="alice")
        result = resolver.resolve("alice")
        assert result == "id-alice"
        loader.assert_called_once()

    def test_resolve_uses_cache_on_second_call(self) -> None:
        """Loader is called once for two resolves of the same key."""
        loader = _make_loader({"alice": "id-alice"})
        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="alice")
        resolver.resolve("alice")
        resolver.resolve("alice")
        loader.assert_called_once()

    def test_resolve_strict_raises_on_miss(self) -> None:
        """VoiceNotFoundError raised with available voices on miss."""
        loader = _make_loader({"alice": "id-alice", "bob": "id-bob"})
        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="alice")
        with pytest.raises(VoiceNotFoundError) as exc_info:
            resolver.resolve("charlie")
        assert exc_info.value.voice_name == "charlie"
        assert "alice" in exc_info.value.available
        assert "bob" in exc_info.value.available

    def test_resolve_lenient_returns_default(self) -> None:
        """strict=False falls back to default_key on miss."""
        loader = _make_loader({"alice": "id-alice", "bob": "id-bob"})
        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="alice")
        result = resolver.resolve("charlie", strict=False)
        assert result == "id-alice"

    def test_resolve_lenient_raises_if_default_missing(self) -> None:
        """strict=False raises if the default key is not in the cache."""
        loader = _make_loader({"bob": "id-bob"})
        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="alice")
        with pytest.raises(VoiceNotFoundError):
            resolver.resolve("charlie", strict=False)

    def test_case_insensitive(self) -> None:
        """resolve('FOO') matches 'foo' in the cache."""
        loader = _make_loader({"foo": "id-foo"})
        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="foo")
        assert resolver.resolve("FOO") == "id-foo"
        assert resolver.resolve("Foo") == "id-foo"


class TestVoiceResolverTTL:
    def test_ttl_triggers_reload(self) -> None:
        """After TTL expires, loader is called again."""
        loader = _make_loader({"alice": "id-alice"})
        resolver: VoiceResolver[str] = VoiceResolver(
            loader, default_key="alice", ttl_seconds=1
        )
        resolver.resolve("alice")
        assert loader.call_count == 1

        # Simulate TTL expiry by backdating _loaded_at
        resolver._loaded_at = time.monotonic() - 2  # pyright: ignore[reportPrivateUsage]
        resolver.resolve("alice")
        assert loader.call_count == 2

    def test_ttl_zero_never_reloads(self) -> None:
        """TTL=0 loads once and never reloads."""
        loader = _make_loader({"alice": "id-alice"})
        resolver: VoiceResolver[str] = VoiceResolver(
            loader, default_key="alice", ttl_seconds=0
        )
        resolver.resolve("alice")
        # Backdate to simulate time passing
        resolver._loaded_at = time.monotonic() - 99999  # pyright: ignore[reportPrivateUsage]
        resolver.resolve("alice")
        assert loader.call_count == 1


class TestVoiceResolverForceRefresh:
    def test_force_refresh_on_cache_miss(self) -> None:
        """A cache miss triggers a force reload."""
        call_count = 0
        voices_v1 = {"alice": "id-alice"}
        voices_v2 = {"alice": "id-alice", "bob": "id-bob"}

        def loader() -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            return voices_v1 if call_count == 1 else voices_v2

        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="alice")
        # First resolve loads v1
        resolver.resolve("alice")
        assert call_count == 1

        # Miss on "bob" triggers force reload which finds bob in v2
        result = resolver.resolve("bob")
        assert result == "id-bob"
        assert call_count == 2

    def test_force_refresh_cooldown(self) -> None:
        """Second miss within cooldown does not trigger reload."""
        loader = _make_loader({"alice": "id-alice"})
        resolver: VoiceResolver[str] = VoiceResolver(
            loader, default_key="alice", cooldown_seconds=60
        )
        resolver.resolve("alice")
        assert loader.call_count == 1

        # First miss triggers force reload
        with pytest.raises(VoiceNotFoundError):
            resolver.resolve("unknown1")
        assert loader.call_count == 2

        # Second miss within cooldown does NOT trigger reload
        with pytest.raises(VoiceNotFoundError):
            resolver.resolve("unknown2")
        assert loader.call_count == 2


class TestVoiceResolverListAll:
    def test_list_all_sorted(self) -> None:
        """list_all returns sorted keys."""
        loader = _make_loader({"charlie": "c", "alice": "a", "bob": "b"})
        resolver: VoiceResolver[str] = VoiceResolver(loader, default_key="alice")
        assert resolver.list_all() == ["alice", "bob", "charlie"]
