"""Shared fixtures for music tests: mock WebSockets and an in-memory store."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocket

from punt_vox.voxd.music.store import FileMeta

__all__ = ["FakeTrackStore"]


class FakeTrackStore:
    """In-memory :class:`TrackStore` for fast, filesystem-free domain tests.

    Satisfies the protocol structurally. ``add`` registers a track by stem
    the way a real generation would; the read methods project the registry
    so pool enumeration, existence, and listing behave like the real store.
    """

    __slots__ = ("_root", "_tracks")

    _root: Path
    _tracks: dict[str, FileMeta]

    def __new__(cls, root: Path = Path("/fake/tracks")) -> Self:
        self = super().__new__(cls)
        self._root = root
        self._tracks = {}
        return self

    def add(self, stem: str, *, size_bytes: int = 1, modified: float = 0.0) -> Path:
        """Register a track named ``stem`` (as a generation would). Return its path."""
        path = self.path_for(stem)
        self._tracks[stem] = FileMeta(
            path=path, size_bytes=size_bytes, modified=modified
        )
        return path

    def tracks_for(self, prefix: str) -> tuple[Path, ...]:
        """Return registered track paths whose stem starts with ``prefix``, sorted."""
        return tuple(
            self._tracks[stem].path
            for stem in sorted(self._tracks)
            if stem.startswith(prefix)
        )

    def listing(self) -> tuple[FileMeta, ...]:
        """Return metadata for every registered track, sorted by stem."""
        return tuple(self._tracks[stem] for stem in sorted(self._tracks))

    def exists(self, stem: str) -> bool:
        """Return whether a track named ``stem`` is registered."""
        return stem in self._tracks

    def path_for(self, stem: str) -> Path:
        """Return the synthetic path for a track named ``stem``."""
        return self._root / f"{stem}.mp3"

    def prepare(self) -> None:
        """No-op: an in-memory store needs no directory."""


@pytest.fixture(autouse=True)
def _music_api_key(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Supply an ElevenLabs key by default for every music test.

    ``MusicScheduler.turn_on`` preflights the provider key and refuses to
    start generation without it. CI runs with ``ELEVENLABS_API_KEY`` unset, so
    without this fixture every ``turn_on``-driven test would raise. Tests that
    assert the missing-key behaviour clear it explicitly with ``monkeypatch``.
    """
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-music-key")


def _make_mock_websocket() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def make_ws() -> Callable[[], MagicMock]:
    """Fixture that returns a factory for mock WebSockets."""
    return _make_mock_websocket
