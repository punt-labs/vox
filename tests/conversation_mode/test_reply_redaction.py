"""Tests for :data:`reply_redactor`."""

from __future__ import annotations

from punt_vox.voxd.conversation_mode.reply_redaction import reply_redactor


def test_redacts_anthropic_style_key() -> None:
    text = (
        "Your key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 -- keep it safe."
    )
    redacted = reply_redactor(text)
    assert "sk-ant-api03" not in redacted
    assert "[redacted]" in redacted


def test_redacts_github_token() -> None:
    text = "found ghp_abcdefghijklmnopqrstuvwxyz0123456789 in the file"
    redacted = reply_redactor(text)
    assert "ghp_" not in redacted
    assert "[redacted]" in redacted


def test_redacts_aws_access_key() -> None:
    text = "AWS_ACCESS_KEY_ID is AKIAABCDEFGHIJKLMNOP"
    redacted = reply_redactor(text)
    assert "AKIAABCDEFGHIJKLMNOP" not in redacted


def test_redacts_key_value_assignment() -> None:
    text = "the .env file has ELEVENLABS_API_KEY=abc123-super-secret-value-here"
    redacted = reply_redactor(text)
    assert "abc123-super-secret-value-here" not in redacted
    assert "[redacted]" in redacted


def test_redacts_high_entropy_run() -> None:
    text = "the token is dGhpc2lzYXZlcnlsb25nYmFzZTY0ZW5jb2RlZHNlY3JldHZhbHVl now"
    redacted = reply_redactor(text)
    assert "dGhpc2lzYXZlcnlsb25nYmFzZTY0ZW5jb2RlZHNlY3JldHZhbHVl" not in redacted


def test_masks_the_userinfo_of_a_url() -> None:
    text = "the remote is https://alice:s3cret@github.com/acme/app.git today"
    assert reply_redactor(text) == (
        "the remote is https://[redacted]@github.com/acme/app.git today"
    )


def test_masks_a_token_used_as_a_url_user() -> None:
    text = "it pushes to https://x-token@gitlab.example.com/acme/app"
    assert (
        reply_redactor(text)
        == "it pushes to https://[redacted]@gitlab.example.com/acme/app"
    )


def test_leaves_a_url_without_userinfo_alone() -> None:
    text = "see https://github.com/acme/app for details"
    assert reply_redactor(text) == text


def test_masks_a_bare_password_assignment_at_the_start() -> None:
    assert reply_redactor("PASSWORD=hunter2") == "[redacted]"


def test_masks_a_bare_password_colon_assignment() -> None:
    assert reply_redactor("password: hunter2 and done") == "[redacted] and done"


def test_a_bare_key_in_speech_is_not_an_assignment() -> None:
    text = "The key: it returns the sum."
    assert reply_redactor(text) == text


def test_passes_through_ordinary_text_unchanged() -> None:
    text = "The weather today is sunny with a high of seventy two degrees."
    assert reply_redactor(text) == text


def test_passes_through_short_technical_text_unchanged() -> None:
    text = "Run `make check` before committing, then push the branch."
    assert reply_redactor(text) == text
