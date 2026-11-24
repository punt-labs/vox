"""Tests for provider voice roster validation and presentation."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from punt_vox.cascade import RosterClient
from punt_vox.types_synthesis_errors import ProviderNotConfiguredError
from punt_vox.voice_roster import RosterError, VoiceRoster


def test_voice_selection_matches_advertised_names_case_insensitively() -> None:
    client = Mock(spec=RosterClient)
    client.voices.side_effect = [["matilda"], ["matilda"]]

    assert VoiceRoster.selection_error(client, "elevenlabs", "Matilda") is None
    assert (
        VoiceRoster.selection_error(client, "elevenlabs", "nobody")
        == "nobody (available: matilda)"
    )


def test_voice_selection_accepts_raw_id_without_fetching_roster() -> None:
    client = Mock(spec=RosterClient)

    assert (
        VoiceRoster.selection_error(client, "elevenlabs", "XrExE9yKIg1WjnnlVkGX")
        is None
    )
    client.voices.assert_not_called()


def test_fetch_rejects_non_string_roster_entries() -> None:
    client = Mock(spec=RosterClient)
    client.voices.side_effect = [[None]]

    assert VoiceRoster.fetch(client, "elevenlabs") == RosterError(
        "voxd returned an invalid voice roster."
    )


def test_provider_check_refuses_provider_removal() -> None:
    check = VoiceRoster.provider_check("elevenlabs")
    check({"provider": "ElevenLabs"})

    with pytest.raises(ProviderNotConfiguredError):
        check({})


def test_featured_excludes_unadvertised_voices_and_other_providers() -> None:
    results = VoiceRoster.featured("elevenlabs", ["matilda", "not-featured"])

    assert [entry["name"] for entry in results] == ["matilda"]
    assert results[0]["blurb"]
    assert VoiceRoster.featured("espeak", ["matilda"]) == []
