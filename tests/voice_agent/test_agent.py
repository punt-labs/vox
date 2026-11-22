from __future__ import annotations

import json
import stat
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.voice_agent import ClientTool, ConvAIControl, ConvAIError, VoiceAgent

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_CLOCK = ClientTool("clock", "Return UTC time", {"type": "object", "properties": {}})
_SIGNED_URL = "ws://127.0.0.1:42609/convai?conversation_signature=not-for-logs"
_COMPLETED_REGISTRATION = "voice-agent.json"
_API_KEY = "localhost-test-key"


@dataclass
class Requests:
    calls: list[tuple[str, str, object]] = field(default_factory=list)
    create_agent_status: int = 200
    delete_tool_status: int = 404
    delete_agent_status: int = 404
    url: str = ""


@pytest.fixture
def wire() -> Iterator[Requests]:
    requests = Requests()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            self._observe(json.loads(self.rfile.read(length)))
            if self.path == "/v1/convai/tools":
                self._reply(200, {"id": "tool_clock"})
            elif self.path == "/v1/convai/agents/create":
                self._reply(requests.create_agent_status, {"agent_id": "agent_vox"})
            else:
                self._reply(500, {})

        def do_GET(self) -> None:
            self._observe(None)
            if self.path == "/v1/convai/conversation/get-signed-url?agent_id=agent_vox":
                self._reply(200, {"signed_url": _SIGNED_URL})
            else:
                self._reply(500, {})

        def do_DELETE(self) -> None:
            self._observe(None)
            if self.path == "/v1/convai/agents/agent_vox?force=true":
                self._reply(
                    requests.delete_agent_status, {"secret": "body not for logs"}
                )
            elif self.path == "/v1/convai/tools/tool_clock?force=true":
                self._reply(
                    requests.delete_tool_status, {"secret": "body not for logs"}
                )
            else:
                self._reply(500, {})

        def _observe(self, body: object) -> None:
            assert self.headers["xi-api-key"] == _API_KEY
            requests.calls.append((self.command, self.path, body))

        def _reply(self, code: int, body: dict[str, object]) -> None:
            response = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        requests.url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            yield requests
        finally:
            server.shutdown()
            thread.join()


def test_lazy_agent_persists_and_reuses_id_then_force_deletes(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _COMPLETED_REGISTRATION
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        assert not config.exists()
        assert not wire.calls
        assert agent.connection((_CLOCK,)) == (_SIGNED_URL, ("tool_clock",))
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        restarted = VoiceAgent(control, path=config)
        assert restarted.connection((_CLOCK,)) == (_SIGNED_URL, ("tool_clock",))
        with pytest.raises(ConvAIError, match="schemas changed"):
            restarted.connection((ClientTool("new", "Different tool"),))
        restarted.teardown()
        restarted.teardown()
    assert not config.exists()
    assert [call[:2] for call in wire.calls] == [
        ("POST", "/v1/convai/tools"),
        ("POST", "/v1/convai/agents/create"),
        ("GET", "/v1/convai/conversation/get-signed-url?agent_id=agent_vox"),
        ("GET", "/v1/convai/conversation/get-signed-url?agent_id=agent_vox"),
        ("DELETE", "/v1/convai/agents/agent_vox?force=true"),
        ("DELETE", "/v1/convai/tools/tool_clock?force=true"),
    ]
    tool = wire.calls[0][2]
    assert tool == {
        "tool_config": {
            "type": "client",
            "name": "clock",
            "description": "Return UTC time",
            "parameters": _CLOCK.parameters,
            "expects_response": True,
        }
    }
    provision = wire.calls[1][2]
    assert isinstance(provision, dict)
    assert provision["platform_settings"]["auth"] == {"enable_auth": True}
    assert provision["platform_settings"]["overrides"][
        "conversation_config_override"
    ] == {
        "agent": {
            "prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": True}
        },
        "conversation": {"text_only": True},
    }
    assert provision["conversation_config"]["agent"]["prompt"]["tool_ids"] == [
        "tool_clock"
    ]
    assert (
        "agent_response_correction"
        in provision["conversation_config"]["conversation"]["client_events"]
    )


def test_partial_provision_is_cleaned_before_retry(
    tmp_path: Path, wire: Requests
) -> None:
    wire.create_agent_status = 403
    config = tmp_path / _COMPLETED_REGISTRATION
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        with pytest.raises(ConvAIError, match="HTTP 403"):
            agent.connection((_CLOCK,))
        assert json.loads(config.read_text())["tool_ids"] == ["tool_clock"]
        wire.create_agent_status = 200
        agent.connection((_CLOCK,))
        assert wire.calls[2][:2] == ("DELETE", "/v1/convai/tools/tool_clock?force=true")


def test_failed_delete_keeps_config_for_retry(tmp_path: Path, wire: Requests) -> None:
    wire.delete_tool_status = 403
    config = tmp_path / _COMPLETED_REGISTRATION
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        agent.connection((_CLOCK,))
        with pytest.raises(ConvAIError, match="HTTP 403") as exc:
            agent.teardown()
        assert "body not for logs" not in str(exc.value)
        assert json.loads(config.read_text())["agent_id"] is None
        wire.delete_tool_status = 200
        agent.teardown()
    assert not config.exists()
    assert (
        wire.calls[-2][:2]
        == wire.calls[-1][:2]
        == ("DELETE", "/v1/convai/tools/tool_clock?force=true")
    )


def test_another_key_cannot_erase_original_workspace_registration(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _COMPLETED_REGISTRATION
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        VoiceAgent(control, path=config).connection((_CLOCK,))
    before = config.read_text()
    assert _API_KEY not in before
    wire.calls.clear()
    with ConvAIControl("different-test-key", base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        with pytest.raises(ConvAIError, match="different ElevenLabs key"):
            agent.connection((_CLOCK,))
        with pytest.raises(ConvAIError, match="different ElevenLabs key"):
            agent.teardown()
    assert not wire.calls
    assert config.read_text() == before


def test_symlink_registration_is_not_used_or_removed(
    tmp_path: Path, wire: Requests
) -> None:
    target = tmp_path / "original.json"
    link = tmp_path / _COMPLETED_REGISTRATION
    link.symlink_to(target)
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        VoiceAgent(control, path=target).connection((_CLOCK,))
        before = target.read_text()
        wire.calls.clear()
        redirected = VoiceAgent(control, path=link)
        with pytest.raises(ConvAIError, match="must not be a symbolic link"):
            redirected.connection((_CLOCK,))
        with pytest.raises(ConvAIError, match="must not be a symbolic link"):
            redirected.teardown()
    assert not wire.calls
    assert link.is_symlink()
    assert target.read_text() == before


def test_provisioning_reports_orphan_id_when_checkpoint_and_cleanup_fail(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_replace = AtomicFile.replace

    def fail_tool_checkpoint(
        file: AtomicFile, text: str, *, mode: int | None = None
    ) -> None:
        if json.loads(text)["tool_ids"]:
            msg = "state directory no longer writable"
            raise PermissionError(msg)
        original_replace(file, text, mode=mode)

    monkeypatch.setattr(AtomicFile, "replace", fail_tool_checkpoint)
    wire.delete_tool_status = 403
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=tmp_path / _COMPLETED_REGISTRATION)
        with pytest.raises(
            ConvAIError, match="tool tool_clock; delete it manually"
        ) as exc:
            agent.connection((_CLOCK,))
    assert isinstance(exc.value.__cause__, ExceptionGroup)
    assert isinstance(exc.value.__cause__.exceptions[0], PermissionError)
    assert isinstance(exc.value.__cause__.exceptions[1], ConvAIError)
