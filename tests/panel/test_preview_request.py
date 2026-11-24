"""Tests for the panel's preview request resolution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from punt_vox.config import ConfigStore
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.preview_request import PreviewRequest
from punt_vox.types_synthesis import SynthesisSpec

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def test_preview_resolves_the_requested_voice_with_the_saved_provider(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path)
    store.write_fields({"provider": "elevenlabs", "voice": "roger"})

    assert PreviewRequest.resolve(store, "aria", _LOGGER) == SynthesisSpec(
        voice="aria", provider="elevenlabs"
    )


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("", "", "⚠ pick a provider from the Provider menu to preview a voice"),
        ("elevenlabs", "unknown-model", "⚠ voxd rejected the request"),
    ],
    ids=["no-provider", "invalid-model"],
)
def test_preview_reports_settings_refusals(
    tmp_path: Path, provider: str, model: str, expected: str
) -> None:
    store = ConfigStore(tmp_path)
    store.write_fields({"provider": provider, "model": model})

    notice = PreviewRequest.resolve(store, "aria", _LOGGER)

    assert isinstance(notice, PanelNotice)
    assert notice.message.startswith(expected)
