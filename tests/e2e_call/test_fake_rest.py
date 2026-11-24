"""Drive the fake ElevenLabs REST stand-in with a plain HTTP client."""

from __future__ import annotations

import socket
import threading

import httpx
import pytest

from punt_vox.voxd.voice_agent.call_overrides import PLACEHOLDER_KEY

from .fake_rest import FakeConvAIRest

_KEY = {"xi-api-key": PLACEHOLDER_KEY}


def _always_alive(_thread: threading.Thread) -> bool:
    return True


def _client(rest: FakeConvAIRest) -> httpx.Client:
    return httpx.Client(base_url=rest.base_url, headers=_KEY, timeout=5)


def test_provisioning_then_signing_is_recorded_in_order() -> None:
    with FakeConvAIRest("ws://127.0.0.1:1/x") as rest, _client(rest) as http:
        tool = http.post("/v1/convai/tools", json={}).json()["id"]
        agent = http.post("/v1/convai/agents/create", json={}).json()["agent_id"]
        signed = http.get(
            "/v1/convai/conversation/get-signed-url", params={"agent_id": agent}
        )
        assert http.delete(f"/v1/convai/tools/{tool}").status_code == 200
        assert http.delete(f"/v1/convai/agents/{agent}").status_code == 200
    assert signed.json() == {"signed_url": "ws://127.0.0.1:1/x"}
    assert [m for m, _ in rest.requests] == ["POST", "POST", "GET", "DELETE", "DELETE"]
    assert rest.errors == []


def test_a_wrong_key_is_refused_and_recorded() -> None:
    with (
        FakeConvAIRest("ws://127.0.0.1:1/x") as rest,
        httpx.Client(base_url=rest.base_url, timeout=5) as http,
    ):
        response = http.post(
            "/v1/convai/tools", json={}, headers={"xi-api-key": "sk-real"}
        )
    assert response.status_code == 401
    assert rest.errors == ["POST /v1/convai/tools: key is not the override placeholder"]


def test_an_unknown_path_is_recorded_as_an_error() -> None:
    with FakeConvAIRest("ws://127.0.0.1:1/x") as rest, _client(rest) as http:
        assert http.get("/v1/convai/nope").status_code == 404
    assert rest.errors == ["GET /v1/convai/nope: unknown path"]


def test_a_forced_delete_of_an_issued_id_is_accepted() -> None:
    """The real client deletes with ``?force=true``; the query hides no id."""
    with FakeConvAIRest("ws://127.0.0.1:1/x") as rest, _client(rest) as http:
        tool = http.post("/v1/convai/tools", json={}).json()["id"]
        agent = http.post("/v1/convai/agents/create", json={}).json()["agent_id"]
        forced = {"force": "true"}
        assert http.delete(f"/v1/convai/tools/{tool}", params=forced).status_code == 200
        agent_delete = http.delete(f"/v1/convai/agents/{agent}", params=forced)
        assert agent_delete.status_code == 200
        unissued = http.delete("/v1/convai/agents/agent_other", params=forced)
        assert unissued.status_code == 404
    assert rest.errors == [
        "DELETE /v1/convai/agents/agent_other?force=true: unknown id",
    ]


def test_deleting_an_id_the_fake_never_issued_is_an_error() -> None:
    with FakeConvAIRest("ws://127.0.0.1:1/x") as rest, _client(rest) as http:
        assert http.delete("/v1/convai/agents/agent_other").status_code == 404
        assert http.delete("/v1/convai/tools/tool_9").status_code == 404
    assert rest.errors == [
        "DELETE /v1/convai/agents/agent_other: unknown id",
        "DELETE /v1/convai/tools/tool_9: unknown id",
    ]


def test_a_handler_failure_is_recorded_not_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bad Content-Length makes the handler raise; the fake keeps the error."""
    request = (
        "POST /v1/convai/tools HTTP/1.1\r\n"
        "Host: fake\r\n"
        f"xi-api-key: {PLACEHOLDER_KEY}\r\n"
        "Content-Length: notanumber\r\n\r\n"
    )
    with FakeConvAIRest("ws://127.0.0.1:1/x") as rest:
        port = int(rest.base_url.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            raw.sendall(request.encode())
            raw.settimeout(5)
            while raw.recv(4096):
                pass
    assert len(rest.errors) == 1
    assert "ValueError" in rest.errors[0]
    assert capsys.readouterr().err == ""


def test_a_server_thread_that_will_not_stop_fails_the_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest = FakeConvAIRest("ws://127.0.0.1:1/x")
    rest.__enter__()
    # Make the join report a still-running thread without hanging the test.
    monkeypatch.setattr(threading.Thread, "is_alive", _always_alive)
    with pytest.raises(RuntimeError, match="fake REST server thread did not stop"):
        rest.__exit__(None, None, None)
