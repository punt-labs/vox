"""Tests for punt_vox.service.keys_env_report — what install tells the operator."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.service.keys_env_report import KeyNotice, KeysEnvReport


def test_kept_notice_says_the_stored_value_survived() -> None:
    notice = KeyNotice("OPENAI_API_KEY", "empty in the environment", kept=True)
    assert str(notice) == (
        "OPENAI_API_KEY: empty in the environment; kept the stored value"
    )


def test_unkept_notice_says_the_provider_has_no_credential() -> None:
    notice = KeyNotice("OPENAI_API_KEY", "refused: a space at position 3", kept=False)
    assert str(notice) == (
        "OPENAI_API_KEY: refused: a space at position 3; "
        "nothing stored, so its provider has no credential"
    )


@pytest.mark.parametrize(
    ("key", "fallback"),
    [
        pytest.param(
            "TTS_PROVIDER",
            "not written; vox picks the first ready provider",
            id="provider",
        ),
        pytest.param(
            "AWS_DEFAULT_REGION",
            "not written; boto3 falls back to AWS_REGION or ~/.aws/config",
            id="region",
        ),
        pytest.param(
            "AWS_PROFILE",
            "not written; boto3 uses the default profile",
            id="profile",
        ),
    ],
)
def test_unkept_setting_names_what_takes_its_place(key: str, fallback: str) -> None:
    notice = KeyNotice(key, "empty in the environment", kept=False)
    assert str(notice) == f"{key}: empty in the environment; {fallback}"


@pytest.mark.parametrize(
    "key",
    [
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ],
)
def test_unkept_credential_says_its_provider_has_no_credential(key: str) -> None:
    notice = KeyNotice(key, "empty in the environment", kept=False)
    assert str(notice).endswith("; nothing stored, so its provider has no credential")


def test_kept_setting_says_the_stored_value_survived() -> None:
    notice = KeyNotice("TTS_PROVIDER", "empty in the environment", kept=True)
    assert str(notice).endswith("; kept the stored value")


def test_report_renders_one_indented_line_per_notice() -> None:
    report = KeysEnvReport(
        Path("/k/keys.env"),
        (
            KeyNotice("AWS_PROFILE", "empty in the environment", kept=True),
            KeyNotice("OPENAI_API_KEY", "empty in the environment", kept=False),
        ),
    )
    unkept = "nothing stored, so its provider has no credential"
    assert report.notice_lines() == (
        "    ! AWS_PROFILE: empty in the environment; kept the stored value",
        f"    ! OPENAI_API_KEY: empty in the environment; {unkept}",
    )
