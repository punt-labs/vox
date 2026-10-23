"""A bad ``SSL_CERT_FILE`` must not stop voxd from booting.

httpx reads ``SSL_CERT_FILE`` when the ElevenLabs SDK client is constructed, not
per request. A daemon that built that client while composing its subsystems died
at boot on a stale CA path, taking speech and chimes down with music. These
tests pin the contract that replaced it: composition never constructs the SDK
client, and the music path reports the bad bundle as a permanent music error
that names the variable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from punt_vox.providers.credentials import ProviderCredentials
from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.voxd.daemon import VoxDaemon
from punt_vox.voxd.programs.music_producer import LengthPolicy, MusicProducer
from punt_vox.voxd.programs.part_tags import PartTags
from punt_vox.voxd.programs.producer import PartSpec, ProducerBadInputError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def missing_ca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``SSL_CERT_FILE`` at a CA bundle that does not exist."""
    cafile = tmp_path / "gone" / "ca-bundle.pem"
    monkeypatch.setenv("SSL_CERT_FILE", str(cafile))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    return cafile


@pytest.fixture
def sdk_builds(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record every construction of the ElevenLabs SDK client."""
    builds: list[dict[str, object]] = []

    def _record(**kwargs: object) -> object:
        builds.append(kwargs)
        return object()

    monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _record)
    return builds


def _spec() -> PartSpec:
    tags = PartTags(title="calm", album="mix", genre="ambient", index=1, total=12)
    return PartSpec(prompt="calm", index=1, tags=tags)


# ---------------------------------------------------------------------------
# The composition root
# ---------------------------------------------------------------------------


def test_build_programs_never_constructs_the_sdk(
    sdk_builds: list[dict[str, object]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composing the Programs subsystem leaves the SDK client unbuilt."""
    monkeypatch.setattr(VoxDaemon, "_programs_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr("punt_vox.voxd.daemon._run_dir", lambda: tmp_path)

    VoxDaemon.build_programs()

    assert sdk_builds == []


def test_build_programs_survives_a_missing_ca_bundle(
    missing_ca: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the real SDK and a bad bundle, composition still completes."""
    monkeypatch.setattr(VoxDaemon, "_programs_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr("punt_vox.voxd.daemon._run_dir", lambda: tmp_path)

    programs = VoxDaemon.build_programs()

    assert programs.service is not None


def test_build_programs_reads_the_key_variable_credentials_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The music key comes from the variable ProviderCredentials maps
    ElevenLabs to -- one source of truth with the readiness gate -- not a
    literal of its own."""
    monkeypatch.setattr(VoxDaemon, "_programs_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr("punt_vox.voxd.daemon._run_dir", lambda: tmp_path)

    def _renamed(_self: ProviderCredentials) -> dict[str, str]:
        return {"elevenlabs": "VOX_TEST_ELEVENLABS_KEY"}

    monkeypatch.setattr(ProviderCredentials, "api_key_env_vars", _renamed)
    monkeypatch.setenv("VOX_TEST_ELEVENLABS_KEY", "from-credentials")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-literal")
    keys: list[str | None] = []

    def _record(*, api_key: str | None = None) -> ElevenLabsMusicProvider:
        keys.append(api_key)
        return ElevenLabsMusicProvider(api_key=api_key)

    monkeypatch.setattr("punt_vox.voxd.daemon.ElevenLabsMusicProvider", _record)

    VoxDaemon.build_programs()

    assert keys == ["from-credentials"]


def test_music_provider_construction_never_constructs_the_sdk(
    sdk_builds: list[dict[str, object]],
) -> None:
    ElevenLabsMusicProvider()

    assert sdk_builds == []


# ---------------------------------------------------------------------------
# The music path reports the bad bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_music_generation_is_a_permanent_error_naming_the_variable(
    missing_ca: Path, tmp_path: Path
) -> None:
    """A bad CA is configuration: retrying cannot help, so it is not transient."""
    producer = MusicProducer(ElevenLabsMusicProvider(), LengthPolicy())

    with pytest.raises(ProducerBadInputError) as info:
        await producer.produce(_spec(), tmp_path / "part.mp3")

    message = str(info.value)
    assert "SSL_CERT_FILE" in message
    assert str(missing_ca) not in message


@pytest.mark.asyncio
async def test_music_generation_leaves_no_partial_file(
    missing_ca: Path, tmp_path: Path
) -> None:
    """The failure happens before any temp file is opened in the target dir."""
    producer = MusicProducer(ElevenLabsMusicProvider(), LengthPolicy())
    target = tmp_path / "out" / "part.mp3"

    with pytest.raises(ProducerBadInputError):
        await producer.produce(_spec(), target)

    assert not target.exists()
    assert not any(target.parent.glob("*.tmp"))
