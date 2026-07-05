"""Tests for the MusicProducer, its error routing, and the LengthPolicy."""

from __future__ import annotations

from pathlib import Path
from typing import Self, final

import pytest
from conftest import _get_valid_mp3_bytes  # pyright: ignore[reportPrivateUsage]
from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.voxd.programs import Part
from punt_vox.voxd.programs.music_producer import LengthPolicy, MusicProducer
from punt_vox.voxd.programs.producer import (
    PartSpec,
    ProducerBadInputError,
    ProducerTransientError,
)


@final
class FakeMusicProvider:
    """A MusicProvider double: writes valid MP3 bytes, or raises a set error."""

    __slots__ = ("_error",)
    _error: Exception | None

    def __new__(cls, error: Exception | None = None) -> Self:
        self = super().__new__(cls)
        self._error = error
        return self

    async def generate_track(
        self, prompt: str, duration_ms: int, output_path: Path
    ) -> Path:
        if self._error is not None:
            raise self._error
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_get_valid_mp3_bytes())
        return output_path


def _producer(error: Exception | None = None) -> MusicProducer:
    return MusicProducer(FakeMusicProvider(error), LengthPolicy())


class TestLengthPolicy:
    def test_sample_within_range(self) -> None:
        policy = LengthPolicy(min_ms=90_000, max_ms=210_000)
        samples = [policy.sample() for _ in range(200)]
        assert all(90_000 <= ms <= 210_000 for ms in samples)

    def test_sample_varies(self) -> None:
        policy = LengthPolicy(min_ms=90_000, max_ms=210_000)
        assert len({policy.sample() for _ in range(50)}) > 1

    def test_single_point_range(self) -> None:
        assert LengthPolicy(min_ms=1000, max_ms=1000).sample() == 1000

    def test_bounds_exposed(self) -> None:
        policy = LengthPolicy(min_ms=100, max_ms=200)
        assert (policy.min_ms, policy.max_ms) == (100, 200)

    def test_rejects_zero_min(self) -> None:
        with pytest.raises(ValueError, match="min_ms must be"):
            LengthPolicy(min_ms=0, max_ms=10)

    def test_rejects_inverted_range(self) -> None:
        with pytest.raises(ValueError, match="must be >= min_ms"):
            LengthPolicy(min_ms=200, max_ms=100)


class TestMusicProducer:
    async def test_produces_ready_part(self, tmp_path: Path) -> None:
        target = tmp_path / "001.mp3"
        part = await _producer().produce(PartSpec(prompt="calm", index=1), target)
        assert part == Part("001.mp3", 1)
        assert target.read_bytes()  # audio actually written

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_permanent_status_is_bad_input(
        self, tmp_path: Path, status: int
    ) -> None:
        producer = _producer(ApiError(status_code=status, body="nope"))
        with pytest.raises(ProducerBadInputError):
            await producer.produce(PartSpec(prompt="x", index=1), tmp_path / "1.mp3")

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_transient_status_is_transient(
        self, tmp_path: Path, status: int
    ) -> None:
        producer = _producer(ApiError(status_code=status, body="later"))
        with pytest.raises(ProducerTransientError):
            await producer.produce(PartSpec(prompt="x", index=1), tmp_path / "1.mp3")

    async def test_unknown_status_is_transient(self, tmp_path: Path) -> None:
        producer = _producer(ApiError(status_code=None, body="?"))
        with pytest.raises(ProducerTransientError):
            await producer.produce(PartSpec(prompt="x", index=1), tmp_path / "1.mp3")

    async def test_timeout_is_transient(self, tmp_path: Path) -> None:
        producer = _producer(TimeoutError("slow"))
        with pytest.raises(ProducerTransientError):
            await producer.produce(PartSpec(prompt="x", index=1), tmp_path / "1.mp3")
