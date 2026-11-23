from __future__ import annotations

import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Self

import pytest

from punt_vox.atomic_file import AtomicFile
from punt_vox.sibling_lock import SiblingLock
from punt_vox.voxd.voice_agent import ClientTool, ConvAIControl, ConvAIError, VoiceAgent

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_CLOCK = ClientTool("clock", "Return UTC time", {"type": "object", "properties": {}})
_SIGNED_URL = "ws://127.0.0.1:42609/convai?conversation_signature=not-for-logs"
_COMPLETED_REGISTRATION = "voice-agent.json"
_API_KEY = "localhost-test-key"


class Requests:
    """Record requests and let tests change the server response between calls."""

    calls: list[tuple[str, str, object]]
    create_tool_id: str
    disconnect_on_tool_creation: bool
    create_agent_status: int
    signed_url_status: int
    delete_tool_status: int
    delete_agent_status: int
    url: str

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.calls = []
        self.create_tool_id = "tool_clock"
        self.disconnect_on_tool_creation = False
        self.create_agent_status = self.signed_url_status = 200
        self.delete_tool_status = self.delete_agent_status = 404
        self.url = ""
        return self


@pytest.fixture
def wire() -> Iterator[Requests]:
    requests = Requests()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            self._observe(json.loads(self.rfile.read(length)))
            if self.path == "/v1/convai/tools":
                if requests.disconnect_on_tool_creation:
                    self.close_connection = True
                    return
                self._reply(200, {"id": requests.create_tool_id})
            elif self.path == "/v1/convai/agents/create":
                self._reply(requests.create_agent_status, {"agent_id": "agent_vox"})
            else:
                self._reply(500, {})

        def do_GET(self) -> None:
            self._observe(None)
            if self.path == "/v1/convai/conversation/get-signed-url?agent_id=agent_vox":
                self._reply(requests.signed_url_status, {"signed_url": _SIGNED_URL})
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
        with pytest.raises(ConvAIError, match="configuration changed"):
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
            "prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": True},
            "first_message": True,
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


def test_changed_agent_settings_require_teardown(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=tmp_path / _COMPLETED_REGISTRATION)
        agent.connection((_CLOCK,))
        monkeypatch.setattr(
            "punt_vox.voxd.voice_agent.control._OVERRIDABLE",
            {"prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": False}},
        )
        with pytest.raises(ConvAIError, match="configuration changed"):
            agent.connection((_CLOCK,))
        assert len(wire.calls) == 3
        agent.teardown()


def test_registration_without_first_message_permission_is_reprovisioned(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _COMPLETED_REGISTRATION
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        with monkeypatch.context() as legacy:
            legacy.setattr(
                "punt_vox.voxd.voice_agent.control._OVERRIDABLE",
                {"prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": True}},
            )
            agent.connection((_CLOCK,))
        wire.calls.clear()

        assert VoiceAgent(control, path=config).connection((_CLOCK,)) == (
            _SIGNED_URL,
            ("tool_clock",),
        )
        assert [method for method, _, _ in wire.calls] == [
            "DELETE",
            "DELETE",
            "POST",
            "POST",
            "GET",
        ]
        wire.calls.clear()
        agent.connection((_CLOCK,))
        assert [method for method, _, _ in wire.calls] == ["GET"]


def test_teardown_first_keeps_new_config_tree_private(tmp_path: Path) -> None:
    state_dir = tmp_path / "state" / "config"
    with ConvAIControl(_API_KEY) as control:
        VoiceAgent(control, path=state_dir / _COMPLETED_REGISTRATION).teardown()
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_dir.parent.stat().st_mode) == 0o700


def test_stale_agent_id_reports_how_to_reprovision(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _COMPLETED_REGISTRATION
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        agent.connection((_CLOCK,))
        wire.signed_url_status = 404
        with pytest.raises(ConvAIError, match="no longer exists; tear down"):
            agent.connection((_CLOCK,))
        assert config.exists()
        agent.teardown()


def test_existing_provisioning_lock_reports_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _COMPLETED_REGISTRATION
    monkeypatch.setattr(SiblingLock, "_ACQUIRE_ATTEMPTS", 1)
    monkeypatch.setattr(SiblingLock, "_ACQUIRE_INTERVAL_S", 0)
    with (
        ConvAIControl(_API_KEY) as control,
        SiblingLock(config).held(),
        pytest.raises(ConvAIError, match="provisioning or teardown is in progress"),
    ):
        VoiceAgent(control, path=config).connection((_CLOCK,))


def test_signed_url_auth_error_explains_required_access(
    tmp_path: Path, wire: Requests
) -> None:
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=tmp_path / _COMPLETED_REGISTRATION)
        agent.connection((_CLOCK,))
        wire.signed_url_status = 403
        with pytest.raises(
            ConvAIError, match="403; check the key's ElevenLabs Agents access"
        ):
            agent.connection((_CLOCK,))


def test_invalid_utf8_registration_is_unreadable(tmp_path: Path) -> None:
    config = tmp_path / _COMPLETED_REGISTRATION
    config.write_bytes(b"\xff")
    with (
        ConvAIControl(_API_KEY) as control,
        pytest.raises(ConvAIError, match="Unreadable Vox voice agent config"),
    ):
        VoiceAgent(control, path=config).teardown()


@pytest.mark.parametrize(
    ("raw", "tool_ids", "error"),
    [
        ("{", [], "Unreadable Vox voice agent config"),
        ("null", [], "Invalid Vox voice agent config"),
        ("record", "clock", "Invalid tool ID list"),
        ("record", [42], "Invalid tool ID in Vox voice agent config"),
    ],
    ids=["bad-json", "no-record", "bad-tool-list", "bad-tool-id"],
)
def test_malformed_registration_is_left_intact(
    tmp_path: Path, raw: str, tool_ids: object, error: str
) -> None:
    config = tmp_path / _COMPLETED_REGISTRATION
    with ConvAIControl(_API_KEY) as control:
        if raw == "record":
            raw = json.dumps(
                {
                    "agent_id": "agent_vox",
                    "tool_ids": tool_ids,
                    "credential_id": control.credential_id,
                    "configuration_hash": "test",
                }
            )
        config.write_text(raw)
        with pytest.raises(ConvAIError, match=error):
            VoiceAgent(control, path=config).teardown()
    assert config.read_text() == raw


@pytest.mark.parametrize("disconnect", [False, True])
def test_unusable_create_response_warns_about_unrecorded_resource(
    tmp_path: Path, wire: Requests, disconnect: bool
) -> None:
    wire.create_tool_id = "unusable/opaque-id"
    wire.disconnect_on_tool_creation = disconnect
    with (
        ConvAIControl(_API_KEY, base_url=wire.url) as control,
        pytest.raises(ConvAIError, match="workspace for an unrecorded") as error,
    ):
        VoiceAgent(control, path=tmp_path / _COMPLETED_REGISTRATION).connection(
            (_CLOCK,)
        )
    assert "unusable/opaque-id" not in str(error.value)


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


@pytest.mark.parametrize("kind", ["tool", "agent"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_provisioning_compensates_after_checkpoint_failure(
    tmp_path: Path,
    wire: Requests,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    cleanup_fails: bool,
) -> None:
    original_replace = AtomicFile.replace
    field = "tool_ids" if kind == "tool" else "agent_id"
    resource_id = "tool_clock" if kind == "tool" else "agent_vox"

    def fail_checkpoint(
        file: AtomicFile, text: str, *, mode: int | None = None
    ) -> None:
        if json.loads(text)[field]:
            msg = "state directory no longer writable"
            raise PermissionError(msg)
        original_replace(file, text, mode=mode)

    monkeypatch.setattr(AtomicFile, "replace", fail_checkpoint)
    wire.delete_tool_status = wire.delete_agent_status = 403 if cleanup_fails else 200
    with ConvAIControl(_API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=tmp_path / _COMPLETED_REGISTRATION)
        if cleanup_fails:
            with pytest.raises(
                ConvAIError, match=f"{kind} {resource_id}; delete it manually"
            ) as exc:
                agent.connection((_CLOCK,))
            assert isinstance(exc.value.__cause__, ExceptionGroup)
            assert isinstance(exc.value.__cause__.exceptions[0], PermissionError)
            assert isinstance(exc.value.__cause__.exceptions[1], ConvAIError)
        else:
            with pytest.raises(PermissionError, match="no longer writable"):
                agent.connection((_CLOCK,))
    assert wire.calls[-1][:2] == (
        "DELETE",
        f"/v1/convai/{kind}s/{resource_id}?force=true",
    )
