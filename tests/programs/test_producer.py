"""Tests for the MusicProducer, its error routing, and the LengthPolicy."""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Self, final

import pytest
from conftest import _get_valid_mp3_bytes  # pyright: ignore[reportPrivateUsage]
from mutagen.id3 import ID3

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.providers.provider_faults import (
    ProviderConfigError,
    ProviderTransportError,
)
from punt_vox.providers.provider_status import ProviderStatusError
from punt_vox.types_provider_errors import ProviderAuthError, ProviderUnavailableError
from punt_vox.voxd.programs import Part
from punt_vox.voxd.programs.music_producer import LengthPolicy, MusicProducer
from punt_vox.voxd.programs.part_tags import PartTags
from punt_vox.voxd.programs.producer import (
    PartSpec,
    ProducerBadInputError,
    ProducerTransientError,
)


def _spec(prompt: str = "x", index: int = 1) -> PartSpec:
    """Build a PartSpec with placeholder ID3 tags for a producer test."""
    tags = PartTags(title=prompt, album="mix", genre="trance", index=index, total=12)
    return PartSpec(prompt=prompt, index=index, tags=tags)


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
        part = await _producer().produce(_spec("calm", 1), target)
        assert part == Part("001.mp3", 1)
        assert target.read_bytes()  # audio actually written

    async def test_writes_id3_tags_on_the_saved_part(self, tmp_path: Path) -> None:
        target = tmp_path / "001.mp3"
        tags = PartTags(
            title="uplifting trance at 138 BPM in A minor",
            album="trance",
            genre="trance",
            index=1,
            total=12,
        )
        await _producer().produce(PartSpec(prompt="calm", index=1, tags=tags), target)
        id3 = ID3(target)
        assert id3["TPE1"].text == ["vox"]  # artist
        assert id3["TPE2"].text == ["vox"]  # album artist
        assert id3["TALB"].text == ["trance"]  # album
        assert id3["TIT2"].text == ["uplifting trance at 138 BPM in A minor"]
        assert id3["TCON"].text == ["trance"]  # genre
        assert id3["TRCK"].text == ["1/12"]  # track/total

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_permanent_status_is_bad_input(
        self, tmp_path: Path, status: int
    ) -> None:
        producer = _producer(ProviderStatusError("elevenlabs", status, "nope"))
        with pytest.raises(ProducerBadInputError):
            await producer.produce(_spec(), tmp_path / "1.mp3")

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_transient_status_is_transient(
        self, tmp_path: Path, status: int
    ) -> None:
        producer = _producer(ProviderStatusError("elevenlabs", status, "later"))
        with pytest.raises(ProducerTransientError):
            await producer.produce(_spec(), tmp_path / "1.mp3")

    async def test_unknown_status_is_transient(self, tmp_path: Path) -> None:
        producer = _producer(ProviderStatusError("elevenlabs", None, None))
        with pytest.raises(ProducerTransientError):
            await producer.produce(_spec(), tmp_path / "1.mp3")

    async def test_timeout_is_transient(self, tmp_path: Path) -> None:
        producer = _producer(TimeoutError("slow"))
        with pytest.raises(ProducerTransientError):
            await producer.produce(_spec(), tmp_path / "1.mp3")

    @pytest.mark.parametrize(
        "error",
        [
            ProviderAuthError("elevenlabs", 401),
            ProviderUnavailableError("elevenlabs", "no ELEVENLABS_API_KEY"),
            CaBundleError("SSL_CERT_FILE names a CA bundle that cannot be loaded"),
            ProviderConfigError("provider request cannot be sent (ProxyError)"),
        ],
        ids=["rejected-key", "missing-key", "ca-bundle", "config"],
    )
    async def test_configuration_failure_is_bad_input(
        self, tmp_path: Path, error: Exception
    ) -> None:
        """A rejected key or a host misconfiguration fails every retry the same."""
        producer = _producer(error)
        with pytest.raises(ProducerBadInputError):
            await producer.produce(_spec(), tmp_path / "1.mp3")

    async def test_real_provider_without_a_key_is_bad_input(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key at boot fails every part the same way: music stops filling
        rather than retrying each part up to the cap."""
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        producer = MusicProducer(ElevenLabsMusicProvider(), LengthPolicy())

        with pytest.raises(ProducerBadInputError, match="ELEVENLABS_API_KEY"):
            await producer.produce(_spec(), tmp_path / "1.mp3")

    async def test_provider_transport_error_is_transient(self, tmp_path: Path) -> None:
        producer = _producer(ProviderTransportError("provider unreachable"))
        with pytest.raises(ProducerTransientError):
            await producer.produce(_spec(), tmp_path / "1.mp3")

    @pytest.mark.parametrize(
        "error",
        [
            PermissionError(errno.EACCES, "Permission denied"),
            OSError(errno.ENOSPC, "No space left on device"),
        ],
        ids=["permission", "disk-full"],
    )
    async def test_local_filesystem_error_escapes_unclassified(
        self, tmp_path: Path, error: OSError
    ) -> None:
        """A local disk fault is not the provider's transport: it must not be
        retried as transient, so it escapes to the filler's unexpected path."""
        producer = _producer(error)
        with pytest.raises(OSError) as info:
            await producer.produce(_spec(), tmp_path / "1.mp3")

        assert info.value is error
