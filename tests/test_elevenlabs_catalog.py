"""Tests for the credential-scoped ElevenLabs catalog index."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from punt_vox.providers.elevenlabs import ElevenLabsProvider
from punt_vox.providers.elevenlabs_catalog import ElevenLabsCatalog
from punt_vox.types import SynthesisRequest


class TestElevenLabsSharedCatalog:
    @pytest.fixture(autouse=True)
    def _isolate_catalogs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ElevenLabsCatalog, "_shared", OrderedDict())

    def test_catalog_uses_the_key_captured_by_each_client(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        first_key, second_key = uuid4().hex, uuid4().hex
        first_id, second_id = "A" * 20, "B" * 20
        clients = {first_key: MagicMock(), second_key: MagicMock()}
        for key, voice_id in ((first_key, first_id), (second_key, second_id)):
            sdk = clients[key]
            sdk.voices.get_all.return_value = SimpleNamespace(
                voices=[SimpleNamespace(name="Shared name", voice_id=voice_id)]
            )
            sdk.text_to_speech.stream.return_value = (b"audio",)

        request = SynthesisRequest(text="hello", voice="shared name")

        def sdk_for_key(*, api_key: str) -> MagicMock:
            return clients[api_key]

        with patch(
            "punt_vox.providers.elevenlabs_client.ElevenLabs", side_effect=sdk_for_key
        ):
            monkeypatch.setenv("ELEVENLABS_API_KEY", first_key)
            first = ElevenLabsProvider()
            monkeypatch.setenv("ELEVENLABS_API_KEY", second_key)
            second = ElevenLabsProvider()
            first.synthesize(request, tmp_path / "first.mp3")
            second.synthesize(request, tmp_path / "second.mp3")

        for key, voice_id in ((first_key, first_id), (second_key, second_id)):
            sdk = clients[key]
            sdk.voices.get_all.assert_called_once()
            assert sdk.text_to_speech.stream.call_args.kwargs["voice_id"] == voice_id

    def test_lru_evicts_oldest_scope_not_recently_used_scope(
        self, monkeypatch: pytest.MonkeyPatch, mock_elevenlabs_client: MagicMock
    ) -> None:
        providers = []
        with patch(
            "punt_vox.providers.elevenlabs_client.ElevenLabs",
            return_value=mock_elevenlabs_client,
        ):
            for _ in range(9):
                monkeypatch.setenv("ELEVENLABS_API_KEY", uuid4().hex)
                providers.append(ElevenLabsProvider())
            for provider in providers[:8]:
                provider.resolve_voice("matilda")
            providers[0].resolve_voice("matilda")
            providers[8].resolve_voice("matilda")
            providers[0].resolve_voice("matilda")
            assert mock_elevenlabs_client.voices.get_all.call_count == 9
            providers[1].resolve_voice("matilda")

        assert mock_elevenlabs_client.voices.get_all.call_count == 10
