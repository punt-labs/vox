"""Tests for :mod:`punt_vox.panel.voice_preview`."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final

import pytest

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.config import VoxConfig
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.voice_preview import VoicePreview
from punt_vox.types_synthesis import SynthesisSpec

if TYPE_CHECKING:
    from punt_vox.panel.ports import PanelDaemonClient, SettingsStore


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

    def __init__(self, provider: str | None) -> None:
        # None: no provider declared, which the session spec refuses.
        self._provider = provider

    def read(self) -> VoxConfig:
        return VoxConfig(
            notify="y",
            speak="y",
            vibe_mode="auto",
            voice="roger",
            provider=self._provider,
            model=None,
            vibe=None,
            vibe_tags=None,
        )

    def write_field(self, key: str, value: str) -> None:
        raise NotImplementedError

    def write_fields(self, updates: dict[str, str]) -> None:
        raise NotImplementedError


def _preview(daemon: _Daemon, provider: str | None = "elevenlabs") -> VoicePreview:
    return VoicePreview(
        cast("PanelDaemonClient", daemon), cast("SettingsStore", _Store(provider))
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


def test_a_real_bug_propagates() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        _preview(_Daemon(RuntimeError("boom"))).play("aria")
