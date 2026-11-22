"""Cloud speech providers must reject bogus credentials without writing audio."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import boto3
import pytest
from botocore.config import Config

from punt_vox.providers.elevenlabs import ElevenLabsProvider
from punt_vox.providers.openai import OpenAIProvider
from punt_vox.providers.polly import PollyProvider
from punt_vox.types import SynthesisRequest
from punt_vox.types_provider_errors import ProviderAuthError

pytestmark = [pytest.mark.integration, pytest.mark.roundtrip]


class TestCloudAuth:
    @pytest.fixture
    def output(self, tmp_path: Path) -> Path:
        return tmp_path / "rejected.mp3"

    @pytest.mark.parametrize(
        ("factory", "key", "voice"),
        [
            (ElevenLabsProvider, "ELEVENLABS_API_KEY", "XrExE9yKIg1WjnnlVkGX"),
            (OpenAIProvider, "OPENAI_API_KEY", "alloy"),
        ],
        ids=["elevenlabs", "openai"],
    )
    def test_invalid_api_key(
        self,
        factory: type[ElevenLabsProvider | OpenAIProvider],
        key: str,
        voice: str,
        output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(key, "vox-nightly-deliberately-invalid")
        provider = factory()
        request = SynthesisRequest(text="Please do not speak this.", voice=voice)

        with pytest.raises(ProviderAuthError) as rejected:
            provider.synthesize(request, output)

        assert rejected.value.provider_name == provider.name
        assert rejected.value.status_code == 401
        assert not output.exists()

    def test_invalid_aws_credentials(self, output: Path) -> None:
        client = boto3.client(
            "polly",
            region_name="us-east-1",
            aws_access_key_id="AKIA0000000000000000",
            aws_secret_access_key="0" * 40,
            config=Config(
                connect_timeout=10, read_timeout=10, retries={"max_attempts": 0}
            ),
        )
        with closing(client):
            provider = PollyProvider(boto_client=client)
            with pytest.raises(ProviderAuthError) as rejected:
                provider.synthesize(
                    SynthesisRequest(text="Please do not speak this."), output
                )

        assert rejected.value.provider_name == "polly"
        assert rejected.value.status_code in {400, 403}
        assert not output.exists()
