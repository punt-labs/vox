"""Load the CPU model only for explicitly requested speech checks."""

from __future__ import annotations

import pytest

from roundtrip.backends import WhisperModel, load_whisper


@pytest.fixture(scope="session")
def whisper_model() -> WhisperModel:
    return load_whisper()
