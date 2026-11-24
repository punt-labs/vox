"""Tests for :mod:`punt_vox.panel.voice_preview`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, cast, final

import pytest

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.config import VoxConfig
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.voice_preview import VoicePreview
from punt_vox.types_synthesis import SynthesisSpec

if TYPE_CHECKING:
    from punt_vox.panel.ports import PanelDaemonClient, SettingsStore

_LOGGER = logging.getLogger("test.voice-preview")


@final
class _Daemon:
    """A daemon that records each synthesis, or raises *error* instead."""

    def __init__(self, error: Exception | None = None) -> None:
        # None: synthesis succeeds and is recorded.
        self._error = error
        self.spoken: list[tuple[str, SynthesisSpec | None]] = []

    def voices(self, provider: str | None = None) -> list[str]:
        raise NotImplementedError

    def synthesize(self, text: str, spec: SynthesisSpec | None = None) -> object:
        if self._error is not None:
            raise self._error
        self.spoken.append((text, spec))
        return {}


@final
class _Store:
    """A store that only ever answers reads, with *provider* configured."""

    _provider: str | None
    _model: str | None

    def __new__(cls, provider: str | None, model: str | None = None) -> Self:
        self = super().__new__(cls)
        # None: no provider declared, which the session spec refuses.
        self._provider = provider
        self._model = model
        return self

    def read(self) -> VoxConfig:
        return VoxConfig(
            notify="y",
            speak="y",
            vibe_mode="auto",
            voice="roger",
            provider=self._provider,
            model=self._model,
            vibe=None,
            vibe_tags=None,
        )

    def write_field(self, key: str, value: str) -> None:
        raise NotImplementedError

    def write_fields(self, updates: dict[str, str]) -> None:
        raise NotImplementedError


def _preview(
    daemon: _Daemon, provider: str | None = "elevenlabs", *, model: str | None = None
) -> VoicePreview:
    return VoicePreview(
        cast("PanelDaemonClient", daemon),
        cast("SettingsStore", _Store(provider, model)),
        _LOGGER,
    )


def test_a_heard_preview_answers_a_silent_notice() -> None:
    assert _preview(_Daemon()).play("aria") == PanelNotice.silent()


def test_the_preview_speaks_the_line_in_the_configured_provider() -> None:
    daemon = _Daemon()

    _preview(daemon).play("aria")

    assert daemon.spoken == [
        ("This is my voice.", SynthesisSpec(voice="aria", provider="elevenlabs"))
    ]


def test_an_unreachable_voxd_answers_unavailable() -> None:
    daemon = _Daemon(VoxdConnectionError("voxd unreachable"))

    assert _preview(daemon).play("aria") == PanelNotice.voxd_unavailable()


def test_an_undeclared_provider_is_refused_before_voxd_is_asked() -> None:
    daemon = _Daemon()

    notice = _preview(daemon, provider=None).play("aria")

    assert (notice.is_present, daemon.spoken) == (True, [])


def test_an_invalid_model_keeps_the_voxd_rejection_notice() -> None:
    daemon = _Daemon()

    notice = _preview(daemon, model="unknown-model").play("aria")

    assert notice.message.startswith("⚠ voxd rejected the request")
    assert "unknown-model" in notice.message
    assert daemon.spoken == []


@pytest.mark.parametrize(
    ("daemon", "provider"),
    [
        (_Daemon(VoxdConnectionError("voxd unreachable")), "elevenlabs"),
        (_Daemon(), None),
    ],
    ids=["unreachable", "refused"],
)
def test_a_failed_preview_warns_under_the_injected_logger(
    caplog: pytest.LogCaptureFixture, daemon: _Daemon, provider: str | None
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER.name):
        _preview(daemon, provider).play("aria")

    assert [(r.name, r.levelno) for r in caplog.records] == [
        (_LOGGER.name, logging.WARNING)
    ]


def test_a_real_bug_propagates() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        _preview(_Daemon(RuntimeError("boom"))).play("aria")
