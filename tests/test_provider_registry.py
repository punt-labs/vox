"""Tests for ProviderRegistry auto-detect logging (§4 gap)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from punt_vox.providers import ProviderRegistry

if TYPE_CHECKING:
    import pytest


class TestAutoDetectLogging:
    """Provider selection logs a deduplicated INFO decision line with its reason."""

    def test_auto_detect_logs_decision_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("TTS_PROVIDER", "say")
        registry = ProviderRegistry()
        with caplog.at_level(logging.INFO, logger="punt_vox.providers"):
            first = registry.auto_detect()
            second = registry.auto_detect()  # same decision -> no second line
        assert first == second == "say"
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert infos == ["provider: auto-detected say (TTS_PROVIDER env var)"]

    def test_aws_probe_failure_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When Polly is not chosen, the AWS probe records why at DEBUG."""
        for key in ("TTS_PROVIDER", "ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(key, raising=False)

        def _no_binary(_name: str, *_a: object, **_k: object) -> str | None:
            return None

        monkeypatch.setattr("punt_vox.providers.shutil.which", _no_binary)
        registry = ProviderRegistry()
        with caplog.at_level(logging.DEBUG, logger="punt_vox.providers"):
            registry.auto_detect()
        debugs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("aws" in m and "polly not chosen" in m for m in debugs)
