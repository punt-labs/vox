from __future__ import annotations

import json
from typing import cast

import httpx
import pytest

from punt_vox.voxd.voice_agent import ClientTool, ConvAIControl, ConvAIError
from punt_vox.voxd.voice_agent.agent_body import AgentBody
from punt_vox.voxd.voice_agent.call_overrides import LOOPBACK_HOSTS, PLACEHOLDER_KEY
from punt_vox.voxd.voice_agent.settings import AgentSettings

_API = "https://api.elevenlabs.io"
_OVERRIDES_LOGGER = "punt_vox.voxd.voice_agent.call_overrides"


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
        AgentBody.fingerprint(
            AgentSettings.minimal(),
            tuple(ClientTool(name, "Return time") for name in names),
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


@pytest.mark.parametrize("override", ["http://127.0.0.1:4321", "http://[::1]:4321"])
def test_base_url_env_var_sends_rest_to_loopback_with_a_placeholder_key(
    override: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A listener on the override port never receives the real API key."""
    monkeypatch.setenv("VOX_CONVAI_BASE_URL", override)
    with (
        caplog.at_level("WARNING", logger=_OVERRIDES_LOGGER),
        ConvAIControl("sk-real-looking-key") as control,
    ):
        assert str(control._http.base_url).rstrip("/") == override
        assert control._http.headers["xi-api-key"] == PLACEHOLDER_KEY
    assert "VOX_CONVAI_BASE_URL" in caplog.text


def test_the_real_key_is_sent_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOX_CONVAI_BASE_URL", raising=False)
    with ConvAIControl("sk-real-looking-key") as control:
        assert str(control._http.base_url).rstrip("/") == _API
        assert control._http.headers["xi-api-key"] == "sk-real-looking-key"


@pytest.mark.parametrize(
    "override",
    [
        "127.0.0.1:4321",  # no scheme
        "https://127.0.0.1:4321",  # not http
        "http://localhost:4321",  # a name, not a loopback IP
        "http://evil.example:4321",  # not loopback
        "http://127.0.0.1",  # no port
        "http://127.0.0.1:notaport",
        "http://127.0.0.1:70000",
        "http://127.0.0.1:4321/v1",  # a path
        "http://user@127.0.0.1:4321",  # userinfo
        "http://:x@127.0.0.1:1",  # a password alone is still userinfo
        "http://user:x@127.0.0.1:1",
        "http://[::1",  # unparsable
        "http://127.0.0.1:8080?",  # an empty query is still a query
        "http://127.0.0.1:8080#",  # an empty fragment is still a fragment
    ],
)
def test_an_invalid_base_url_env_var_fails_the_call_by_name(
    override: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set but wrong override never falls back to production with the real key."""
    monkeypatch.setenv("VOX_CONVAI_BASE_URL", override)
    with pytest.raises(ValueError, match="VOX_CONVAI_BASE_URL"):
        ConvAIControl("sk-real-looking-key")


def test_loopback_hosts_are_literal_ips_only() -> None:
    assert sorted(LOOPBACK_HOSTS) == ["127.0.0.1", "::1"]


def test_the_agent_is_created_with_the_built_in_end_call_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider ends the call for the agent; the registration asks for it."""
    bodies: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        bodies.append(cast("dict[str, object]", json.loads(request.content)))
        return httpx.Response(200, json={"agent_id": "agent_vox"})

    with (
        httpx.Client(
            transport=httpx.MockTransport(respond), base_url="https://test"
        ) as client,
        ConvAIControl("test-key") as control,
        monkeypatch.context() as patch,
    ):
        patch.setattr(control, "_http", client)
        assert control.create_agent(("tool_1",)) == "agent_vox"
    (body,) = bodies
    config = cast("dict[str, object]", body["conversation_config"])
    agent = cast("dict[str, object]", config["agent"])
    prompt = cast("dict[str, object]", agent["prompt"])
    assert prompt["tool_ids"] == ["tool_1"]
    built_in = cast("dict[str, object]", prompt["built_in_tools"])
    end_call = cast("dict[str, object]", built_in["end_call"])
    assert end_call["type"] == "system"
    assert end_call["name"] == "end_call"
    assert end_call["params"] == {"system_tool_type": "end_call"}
    assert "closing sentence" in str(end_call["description"])
