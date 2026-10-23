"""Tests for punt_vox.service.keys_env_merge -- what install takes from the env."""

from __future__ import annotations

from punt_vox.service.keys_env_merge import KeysEnvMerge
from punt_vox.service.keys_env_report import KeyNotice


def test_new_value_overrides_the_stored_one() -> None:
    merge = KeysEnvMerge({"OPENAI_API_KEY": "old"}, {"OPENAI_API_KEY": "sk-new"})
    assert merge.merged == {"OPENAI_API_KEY": "sk-new"}
    assert merge.notices == ()


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
    merge = KeysEnvMerge.over_text(text, {"OPENAI_API_KEY": "sk-new"})
    assert merge.merged == {"AWS_PROFILE": "dev", "OPENAI_API_KEY": "sk-new"}
