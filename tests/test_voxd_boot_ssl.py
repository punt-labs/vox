"""A bad ``SSL_CERT_FILE`` must not stop voxd from booting.

httpx reads ``SSL_CERT_FILE`` when the ElevenLabs SDK client is constructed, not
per request. A daemon that built that client while composing its subsystems died
at boot on a stale CA path, taking speech and chimes down with music. These
tests pin the contract that replaced it: composition never touches the TLS stack,
and the music path -- the only one that needs the client at boot time -- reports
the bad bundle as a permanent music error that names the variable and the path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from punt_vox.providers.elevenlabs_client import CaBundleError, ElevenLabsClient
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
def garbage_ca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``SSL_CERT_FILE`` at a file that exists but holds no certificate."""
    cafile = tmp_path / "ca-bundle.pem"
    cafile.write_text("this is not a PEM certificate\n")
    monkeypatch.setenv("SSL_CERT_FILE", str(cafile))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    return cafile


def _spec() -> PartSpec:
    tags = PartTags(title="calm", album="mix", genre="ambient", index=1, total=12)
    return PartSpec(prompt="calm", index=1, tags=tags)


# ---------------------------------------------------------------------------
# The composition root
# ---------------------------------------------------------------------------


def test_build_programs_survives_a_missing_ca_bundle(
    missing_ca: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Composing the Programs subsystem never constructs the SDK client."""
    monkeypatch.setattr(VoxDaemon, "_programs_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr("punt_vox.voxd.daemon._run_dir", lambda: tmp_path)

    programs = VoxDaemon.build_programs()

    assert programs.service is not None
    assert not missing_ca.exists()


def test_music_provider_construction_survives_a_missing_ca_bundle(
    missing_ca: Path,
) -> None:
    """The provider defers the client, so building it cannot read the bundle."""
    ElevenLabsMusicProvider()

    assert not missing_ca.exists()


# ---------------------------------------------------------------------------
# The music path reports the bad bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_music_generation_is_a_permanent_error_naming_the_bundle(
    missing_ca: Path, tmp_path: Path
) -> None:
    """A bad CA is configuration: retrying cannot help, so it is not transient."""
    producer = MusicProducer(ElevenLabsMusicProvider(), LengthPolicy())

    with pytest.raises(ProducerBadInputError) as info:
        await producer.produce(_spec(), tmp_path / "part.mp3")

    message = str(info.value)
    assert "SSL_CERT_FILE" in message
    assert str(missing_ca) in message


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


# ---------------------------------------------------------------------------
# ElevenLabsClient -- the one place the SDK client is built
# ---------------------------------------------------------------------------


def test_client_names_a_missing_bundle(missing_ca: Path) -> None:
    with pytest.raises(CaBundleError) as info:
        ElevenLabsClient().sdk()

    message = str(info.value)
    assert "SSL_CERT_FILE" in message
    assert str(missing_ca) in message


def test_client_names_a_bundle_with_no_certificate(garbage_ca: Path) -> None:
    """An unparseable bundle is the same operator mistake as a missing one."""
    with pytest.raises(CaBundleError) as info:
        ElevenLabsClient().sdk()

    assert str(garbage_ca) in str(info.value)


def test_client_bundle_error_is_a_value_error(missing_ca: Path) -> None:
    """ValueError routes verbatim through the wire reply as a rejection."""
    with pytest.raises(ValueError, match="SSL_CERT_FILE"):
        ElevenLabsClient().sdk()


def test_client_bundle_error_chains_the_tls_cause(missing_ca: Path) -> None:
    with pytest.raises(CaBundleError) as info:
        ElevenLabsClient().sdk()

    assert isinstance(info.value.__cause__, OSError)


def test_client_without_ssl_cert_file_reraises_os_error_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no SSL_CERT_FILE set, an OSError is not ours to rename."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    cause = PermissionError("denied")

    def _raise(**_kwargs: object) -> object:
        raise cause

    monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _raise)

    with pytest.raises(PermissionError) as info:
        ElevenLabsClient().sdk()

    assert info.value is cause


def test_client_retries_the_build_after_a_failure(
    missing_ca: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed build is not cached: fixing the bundle lets the next use succeed."""
    client = ElevenLabsClient()
    with pytest.raises(CaBundleError):
        client.sdk()

    monkeypatch.delenv("SSL_CERT_FILE")

    assert client.sdk() is not None


def test_client_builds_the_sdk_once(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[object] = []

    def _fake(**_kwargs: object) -> object:
        built.append(object())
        return built[-1]

    monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _fake)
    client = ElevenLabsClient()

    first = client.sdk()
    second = client.sdk()

    assert first is second
    assert len(built) == 1


def test_client_returns_an_injected_sdk_without_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(**_kwargs: object) -> object:
        raise AssertionError

    monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _forbidden)
    sdk = MagicMock()

    assert ElevenLabsClient(sdk=sdk).sdk() is sdk


def test_client_reads_the_api_key_at_build_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key comes from the environment when the client is first used.

    The SDK's own ``api_key`` default is evaluated when the SDK module is
    imported, so a key loaded into the environment afterwards -- voxd loads its
    keys file after its modules import -- would never reach a bare
    ``ElevenLabs()``.
    """
    captured: dict[str, object] = {}

    def _fake(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("punt_vox.providers.elevenlabs_client.ElevenLabs", _fake)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    client = ElevenLabsClient()
    monkeypatch.setenv("ELEVENLABS_API_KEY", "loaded-late")

    client.sdk()

    assert captured["api_key"] == "loaded-late"
