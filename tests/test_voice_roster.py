"""Tests for provider voice roster validation and presentation."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from punt_vox.types_synthesis_errors import ProviderNotConfiguredError
from punt_vox.voice_roster import RosterError, VoiceRoster
from punt_vox.voice_target import RosterClient, VoiceTarget


def test_voice_selection_matches_advertised_names_case_insensitively() -> None:
    client = Mock(spec=RosterClient)
    client.voices.side_effect = [["matilda"], ["matilda"]]

    assert (
        VoiceRoster.selection_error(client, VoiceTarget("elevenlabs"), "Matilda")
        is None
    )
    assert (
        VoiceRoster.selection_error(client, VoiceTarget("elevenlabs"), "nobody")
        == "Unknown voice 'nobody'. Available: ['matilda']"
    )


def test_voice_selection_accepts_raw_id_without_fetching_roster() -> None:
    client = Mock(spec=RosterClient)

    assert (
        VoiceRoster.selection_error(
            client, VoiceTarget("elevenlabs"), "XrExE9yKIg1WjnnlVkGX"
        )
        is None
    )
    client.voices.assert_not_called()


def test_fetch_rejects_non_string_roster_entries() -> None:
    client = Mock(spec=RosterClient)
    client.voices.side_effect = [[None]]

    assert VoiceRoster.fetch(client, VoiceTarget("elevenlabs")) == RosterError(
        "voxd returned an invalid voice roster."
    )


def test_provider_check_refuses_provider_removal() -> None:
    check = VoiceRoster.selection_check(VoiceTarget("elevenlabs"))
    check({"provider": "ElevenLabs"})

    with pytest.raises(ProviderNotConfiguredError):
        check({})


def test_selection_check_refuses_model_change() -> None:
    check = VoiceRoster.selection_check(VoiceTarget("openai", "gpt-4o-mini-tts"))
    check({"provider": "openai", "model": "gpt-4o-mini-tts"})

    with pytest.raises(ValueError, match="The active model changed"):
        check({"provider": "openai", "model": "tts-1"})


def test_featured_excludes_unadvertised_voices_and_other_providers() -> None:
    results = VoiceRoster.featured("elevenlabs", ["matilda", "not-featured"])

    assert [entry["name"] for entry in results] == ["matilda"]
    assert results[0]["blurb"]
    assert VoiceRoster.featured("espeak", ["matilda"]) == []
