"""Test identity derivation for ElevenLabs catalog sharing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from punt_vox.providers.elevenlabs_credential import ElevenLabsCredential

if TYPE_CHECKING:
    import pytest


def test_catalog_scope_tracks_the_captured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "first-key")
    first = ElevenLabsCredential(None, injected_sdk=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "next-key")
    same_key = ElevenLabsCredential("first-key", injected_sdk=False)
    rotated = ElevenLabsCredential(None, injected_sdk=False)

    assert first.require_key() == "first-key"
    assert first.catalog_scope is not None
    assert first.catalog_scope == same_key.catalog_scope
    assert first.catalog_scope != rotated.catalog_scope


def test_injected_sdk_never_uses_a_shared_scope() -> None:
    assert ElevenLabsCredential("key", injected_sdk=True).catalog_scope is None


def test_missing_key_has_no_shared_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert ElevenLabsCredential(None, injected_sdk=False).catalog_scope is None
