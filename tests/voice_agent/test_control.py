from __future__ import annotations

import httpx
import pytest

from punt_vox.voxd.voice_agent import ClientTool, ConvAIControl, ConvAIError


def test_credential_identity_uses_scrypt_scoped_to_the_endpoint() -> None:
    with (
        ConvAIControl("test-key") as control,
        ConvAIControl("test-key", base_url="http://127.0.0.1:1") as local,
    ):
        assert control.credential_id == (
            "243f5962451f4d59237848eaa523ceac270484dc29ec063b5151486d152c1b17954"
            "74874cc5e8b7c1266618f2787728b5693ec4272601aff3a2dc0cb5d8a30cf"
        )
        assert local.credential_id != control.credential_id


@pytest.mark.parametrize("resource_id", ["", "tool/other", "tool?force=false"])
def test_control_rejects_invalid_resource_ids_before_request(resource_id: str) -> None:
    with (
        ConvAIControl("test-key", base_url="http://127.0.0.1:1") as control,
        pytest.raises(ConvAIError, match="Invalid ElevenLabs resource ID"),
    ):
        control.delete_tool(resource_id)


def test_blank_api_key_is_rejected_before_connecting() -> None:
    with pytest.raises(ValueError, match="requires an API key"):
        ConvAIControl(" ")


@pytest.mark.parametrize("names", [("",), ("clock", "clock")])
def test_tool_catalog_requires_distinct_nonempty_names(names: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="nonempty and unique"):
        ConvAIControl.registration_fingerprint(
            tuple(ClientTool(name, "Return time") for name in names)
        )


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"not JSON: private body", "invalid JSON"),
        (b"[]", "missing signed_url"),
        (b'{"signed_url": ""}', "missing signed_url"),
    ],
)
def test_missing_or_malformed_signed_url_is_not_exposed(
    monkeypatch: pytest.MonkeyPatch, body: bytes, error: str
) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with (
        httpx.Client(
            transport=httpx.MockTransport(respond), base_url="https://test"
        ) as client,
        ConvAIControl("test-key") as control,
    ):
        with monkeypatch.context() as patch:
            patch.setattr(control, "_http", client)
            with pytest.raises(ConvAIError, match=error) as failure:
                control.signed_url("agent_vox")
        assert "private" not in str(failure.value)


def test_signed_url_connection_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def disconnect(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("private vendor detail")

    with (
        httpx.Client(
            transport=httpx.MockTransport(disconnect), base_url="https://test"
        ) as client,
        ConvAIControl("test-key") as control,
    ):
        with monkeypatch.context() as patch:
            patch.setattr(control, "_http", client)
            with pytest.raises(
                ConvAIError, match=r"get-signed-url failed \(ReadError\)"
            ) as failure:
                control.signed_url("agent_vox")
        assert "private" not in str(failure.value)
        assert "unrecorded" not in str(failure.value)
