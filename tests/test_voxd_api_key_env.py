"""Tests for punt_vox.voxd.api_key_env -- a per-call key set for one block."""

from __future__ import annotations

import os

import pytest

from punt_vox.voxd.api_key_env import ApiKeyEnv

_VAR = "OPENAI_API_KEY"


def test_sets_the_key_for_the_block_and_restores_the_previous_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_VAR, "daemon-key")

    with ApiKeyEnv("openai", "caller-key").applied():
        inside = os.environ[_VAR]

    assert (inside, os.environ[_VAR]) == ("caller-key", "daemon-key")


def test_removes_a_key_that_was_not_set_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_VAR, raising=False)

    with ApiKeyEnv("openai", "caller-key").applied():
        pass

    assert _VAR not in os.environ


def test_restores_the_previous_key_when_the_block_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that fails to build must not leave the caller's key behind."""
    monkeypatch.setenv(_VAR, "daemon-key")
    error = "provider failed"

    with pytest.raises(RuntimeError, match=error), ApiKeyEnv("openai", "k").applied():
        raise RuntimeError(error)

    assert os.environ[_VAR] == "daemon-key"


@pytest.mark.parametrize(
    ("provider", "api_key"),
    [("openai", None), ("openai", ""), ("polly", "caller-key"), ("", "caller-key")],
    ids=["no-key", "empty-key", "provider-reads-no-key", "no-provider"],
)
def test_changes_nothing_without_a_key_for_a_provider_that_reads_one(
    provider: str, api_key: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_VAR, "daemon-key")
    before = dict(os.environ)
    key_env = ApiKeyEnv(provider, api_key)

    with key_env.applied():
        inside = dict(os.environ)

    assert not key_env.injects
    assert inside == before


@pytest.mark.parametrize("provider", ["openai", "elevenlabs"])
def test_injects_a_key_for_a_provider_that_reads_one(provider: str) -> None:
    assert ApiKeyEnv(provider, "caller-key").injects
