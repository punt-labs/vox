"""Tests for punt_vox.service.keys_env_merge -- what install takes from the env."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.service.keys_env_merge import KeysEnvMerge
from punt_vox.service.keys_env_report import KeyNotice

_DIFFERS = "the environment's value differs (--rotate-keys replaces it)"


def test_a_different_value_keeps_the_stored_one_and_notes_it() -> None:
    merge = KeysEnvMerge({"OPENAI_API_KEY": "sk-old"}, {"OPENAI_API_KEY": "sk-new"})
    assert merge.merged == {"OPENAI_API_KEY": "sk-old"}
    assert merge.notices == (KeyNotice("OPENAI_API_KEY", _DIFFERS, kept=True),)
    assert merge.rotated == ()


def test_rotate_replaces_a_different_stored_value_and_names_it() -> None:
    merge = KeysEnvMerge(
        {"OPENAI_API_KEY": "sk-old"}, {"OPENAI_API_KEY": "sk-new"}, rotate=True
    )
    assert merge.merged == {"OPENAI_API_KEY": "sk-new"}
    assert merge.notices == ()
    assert merge.rotated == ("OPENAI_API_KEY",)


@pytest.mark.parametrize("key", ["AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID"])
def test_a_different_aws_credential_is_kept_without_rotate(key: str) -> None:
    merge = KeysEnvMerge({key: "stored"}, {key: "shell"})
    assert merge.merged == {key: "stored"}
    assert merge.notices == (KeyNotice(key, _DIFFERS, kept=True),)


@pytest.mark.parametrize("key", ["TTS_PROVIDER", "AWS_PROFILE", "AWS_DEFAULT_REGION"])
def test_a_different_setting_follows_the_shell_without_rotate(key: str) -> None:
    merge = KeysEnvMerge({key: "stored"}, {key: "shell"})
    assert merge.merged == {key: "shell"}
    assert merge.notices == ()
    assert merge.rotated == ()


def test_the_same_value_in_both_places_is_silent() -> None:
    merge = KeysEnvMerge({"OPENAI_API_KEY": "sk-k"}, {"OPENAI_API_KEY": " sk-k "})
    assert merge.merged == {"OPENAI_API_KEY": "sk-k"}
    assert merge.notices == ()
    assert merge.rotated == ()


def test_a_new_key_is_written_without_rotate() -> None:
    merge = KeysEnvMerge({}, {"OPENAI_API_KEY": "sk-new"})
    assert merge.merged == {"OPENAI_API_KEY": "sk-new"}
    assert merge.notices == ()
    assert merge.rotated == ()


def test_rotate_never_writes_an_empty_or_refused_value() -> None:
    stored = {"ELEVENLABS_API_KEY": "el-old", "OPENAI_API_KEY": "sk-old"}
    env = {"ELEVENLABS_API_KEY": "", "OPENAI_API_KEY": "sk bad"}
    merge = KeysEnvMerge(stored, env, rotate=True)
    assert merge.merged == stored
    assert merge.rotated == ()
    assert [n.kept for n in merge.notices] == [True, True]


def test_unknown_environment_names_are_ignored() -> None:
    merge = KeysEnvMerge({}, {"HOME": "/home/x", "PATH": "/bin"})
    assert merge.merged == {}
    assert merge.notices == ()


def test_stored_keys_absent_from_the_env_survive() -> None:
    merge = KeysEnvMerge({"AWS_PROFILE": "dev"}, {})
    assert merge.merged == {"AWS_PROFILE": "dev"}


def test_empty_value_keeps_the_stored_one_and_notes_it() -> None:
    merge = KeysEnvMerge({"OPENAI_API_KEY": "sk-old"}, {"OPENAI_API_KEY": "  "})
    assert merge.merged == {"OPENAI_API_KEY": "sk-old"}
    assert merge.notices == (
        KeyNotice("OPENAI_API_KEY", "empty in the environment", kept=True),
    )


def test_empty_value_with_nothing_stored_is_noted_as_unkept() -> None:
    merge = KeysEnvMerge({}, {"OPENAI_API_KEY": ""})
    assert merge.merged == {}
    assert merge.notices == (
        KeyNotice("OPENAI_API_KEY", "empty in the environment", kept=False),
    )


def test_a_stored_empty_line_does_not_count_as_kept() -> None:
    merge = KeysEnvMerge({"OPENAI_API_KEY": ""}, {"OPENAI_API_KEY": ""})
    assert merge.notices[0].kept is False


def test_refused_value_names_the_reason() -> None:
    merge = KeysEnvMerge({}, {"OPENAI_API_KEY": "sk ab"})
    (notice,) = merge.notices
    assert (
        notice.reason == "refused: a space at position 3 (no HTTP header may carry it)"
    )


def test_notices_come_in_key_order() -> None:
    merge = KeysEnvMerge({}, {"OPENAI_API_KEY": "", "AWS_PROFILE": ""})
    assert [n.key for n in merge.notices] == ["AWS_PROFILE", "OPENAI_API_KEY"]


def test_over_text_parses_the_stored_file() -> None:
    text = "# header\nAWS_PROFILE=dev\nOPENAI_API_KEY=sk-old\n"
    merge = KeysEnvMerge.over_text(text, {"TTS_PROVIDER": "say"})
    assert merge.merged == {
        "AWS_PROFILE": "dev",
        "OPENAI_API_KEY": "sk-old",
        "TTS_PROVIDER": "say",
    }


def test_over_text_passes_rotate_through() -> None:
    text = "OPENAI_API_KEY=sk-old\n"
    merge = KeysEnvMerge.over_text(text, {"OPENAI_API_KEY": "sk-new"}, rotate=True)
    assert merge.merged == {"OPENAI_API_KEY": "sk-new"}


def test_text_is_the_header_then_sorted_lines() -> None:
    merge = KeysEnvMerge({"TTS_PROVIDER": "say"}, {"AWS_PROFILE": "dev"})
    header, _, body = merge.text().partition("\n\n")
    assert header.startswith("# vox provider keys")
    assert body == "AWS_PROFILE=dev\nTTS_PROVIDER=say\n"


def test_report_carries_the_path_and_the_notices() -> None:
    merge = KeysEnvMerge({}, {"OPENAI_API_KEY": ""})
    report = merge.report(Path("/k/keys.env"))
    assert report.path == Path("/k/keys.env")
    assert report.notices == merge.notices
    assert report.rotated == merge.rotated
