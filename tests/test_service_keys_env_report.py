"""Tests for punt_vox.service.keys_env_report — what install tells the operator."""

from __future__ import annotations

from punt_vox.service.keys_env_report import KeyNotice


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
