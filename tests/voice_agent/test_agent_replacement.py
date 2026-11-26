"""A replacement agent that fails or is interrupted leaves no remote orphans."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.voice_agent import ClientTool, ConvAIControl, ConvAIError, VoiceAgent
from punt_vox.voxd.voice_agent.disposal import Disposal
from punt_vox.voxd.voice_agent.fault import ForeignResourceError
from punt_vox.voxd.voice_agent.host_marker import HostMarker

from .fake_wire import API_KEY, SIGNED_URL

if TYPE_CHECKING:
    from pathlib import Path

    from .fake_wire import Requests

_CLOCK = ClientTool("clock", "Return UTC time")
_ALARM = ClientTool("alarm", "Ring a bell")
_REGISTRATION = "voice-agent.json"
_FOREIGN_DESCRIPTION = "Someone else's tool, not made by voxd"


class Died(BaseException):
    """Escape every ``except Exception``, as a killed process would."""


def _write_registration(
    path: Path, control: ConvAIControl, **fields: object
) -> dict[str, object]:
    record: dict[str, object] = {
        "agent_id": None,
        "tool_ids": [],
        "configuration_hash": "",
        "credential_id": control.credential_id,
        "retired_agents": [],
        "retired_tools": [],
        "pending_agents": [],
        "pending_tools": [],
    }
    record.update(fields)
    path.write_text(json.dumps(record))
    return record


def _stored(path: Path) -> dict[str, object]:
    record: dict[str, object] = json.loads(path.read_text())
    return record


# -- Invariant 3: a delete verifies voxd's marker and this host's marker ------


def test_a_tool_without_voxds_marker_is_never_deleted(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.tools["tool_foreign"] = _FOREIGN_DESCRIPTION
    wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        _write_registration(config, control, retired_tools=["tool_foreign"])
        VoiceAgent(control, path=config).retire_stale()
    assert wire.deletes() == []
    assert wire.verifications == ["tool tool_foreign"]
    assert _stored(config)["retired_tools"] == ["tool_foreign"]


def test_an_agent_without_voxds_marker_is_never_deleted(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.agents["agent_foreign"] = "Someone else's agent"
    wire.delete_agent_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        _write_registration(config, control, retired_agents=["agent_foreign"])
        VoiceAgent(control, path=config).retire_stale()
    assert wire.deletes() == []
    assert _stored(config)["retired_agents"] == ["agent_foreign"]


def test_a_registration_copied_from_another_host_deletes_nothing(
    tmp_path: Path, wire: Requests
) -> None:
    """Resources voxd marked on host A are not host B's to delete."""
    first, second = tmp_path / "a" / _REGISTRATION, tmp_path / "b" / _REGISTRATION
    wire.delete_agent_status = wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        VoiceAgent(control, path=first).connection((_CLOCK,))
        second.parent.mkdir()
        second.write_text(first.read_text())
        with pytest.raises(ConvAIError, match="lacks this host's marker"):
            VoiceAgent(control, path=second).teardown()
    assert wire.deletes() == []


def test_a_tampered_registration_cannot_delete_a_foreign_resource_at_teardown(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.tools["tool_foreign"] = _FOREIGN_DESCRIPTION
    wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        _write_registration(config, control, tool_ids=["tool_foreign"])
        with pytest.raises(ConvAIError, match=r"tool tool_foreign: lacks"):
            VoiceAgent(control, path=config).teardown()
    assert wire.deletes() == []


def test_teardown_deletes_what_it_can_and_names_each_id_it_could_not(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.tools["tool_foreign"] = _FOREIGN_DESCRIPTION
    wire.delete_forgets = True
    wire.delete_agent_status = wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        agent.connection((_CLOCK,))
        record = _stored(config)
        record["retired_tools"] = ["tool_foreign"]
        config.write_text(json.dumps(record))
        with pytest.raises(ConvAIError) as caught:
            agent.teardown()
    text = str(caught.value)
    assert "tool tool_foreign: lacks this host's marker; not deleted" in text
    assert "tool_clock" not in text
    assert wire.tools == {"tool_foreign": _FOREIGN_DESCRIPTION}
    assert wire.agents == {}
    assert _stored(config)["retired_tools"] == ["tool_foreign"]


def test_a_teardown_that_deleted_everything_reports_nothing_and_removes_the_file(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.delete_forgets = True
    wire.delete_agent_status = wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config)
        agent.connection((_CLOCK,))
        agent.teardown()
    assert not config.exists()
    assert wire.tools == {} and wire.agents == {}


def test_a_resource_that_is_already_gone_is_not_deleted_again(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        _write_registration(config, control, retired_tools=["tool_missing"])
        VoiceAgent(control, path=config).retire_stale()
    assert wire.deletes() == []
    assert _stored(config)["retired_tools"] == []


def test_every_delete_is_preceded_by_a_marker_check(
    tmp_path: Path, wire: Requests
) -> None:
    wire.delete_agent_status = wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=tmp_path / _REGISTRATION)
        agent.connection((_CLOCK,))
        agent.teardown()
    assert wire.verifications == ["agent agent_vox", "tool tool_clock"]
    assert len(wire.deletes()) == 2


@pytest.mark.parametrize(
    "body",
    [b"<html>gateway error</html>", b"{}", b'{"tool_config": {"description": 7}}'],
    ids=["not-json", "field-missing", "field-not-text"],
)
def test_an_unreadable_lookup_is_retryable_not_foreign(
    tmp_path: Path, wire: Requests, body: bytes
) -> None:
    config = tmp_path / _REGISTRATION
    wire.tools["tool_clock"] = "Return UTC time"
    wire.lookup_body = body
    wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        _write_registration(config, control, retired_tools=["tool_clock"])
        with pytest.raises(ConvAIError) as caught:
            control.delete_tool("tool_clock", "[marker]")
        assert not isinstance(caught.value, ForeignResourceError)
        VoiceAgent(control, path=config).retire_stale()
    assert wire.deletes() == []
    assert _stored(config)["retired_tools"] == ["tool_clock"]


def test_a_lookup_that_shows_text_without_the_marker_is_foreign(
    tmp_path: Path, wire: Requests
) -> None:
    wire.tools["tool_x"] = _FOREIGN_DESCRIPTION
    with (
        ConvAIControl(API_KEY, base_url=wire.url) as control,
        pytest.raises(ForeignResourceError),
    ):
        control.delete_tool("tool_x", "[marker]")


# -- Invariant 1: a failed replacement leaves the account as it was -----------


def _provision_clock(
    control: ConvAIControl, config: Path, wire: Requests
) -> VoiceAgent:
    agent = VoiceAgent(control, path=config)
    assert agent.connection((_CLOCK,)) == (SIGNED_URL, ("tool_clock",))
    wire.calls.clear()
    wire.verifications.clear()
    return agent


def test_a_failed_agent_create_deletes_every_tool_the_attempt_created(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        before = dict(wire.tools), dict(wire.agents)
        wire.delete_forgets = True
        wire.delete_agent_status = wire.delete_tool_status = 200
        wire.next_tool_ids = ["tool_alarm", "tool_bell"]
        wire.create_agent_status = 403
        with pytest.raises(ConvAIError, match="HTTP 403"):
            agent.connection((_ALARM, ClientTool("bell", "Ring twice")))
    assert (wire.tools, wire.agents) == before


def test_a_failed_replacement_keeps_the_old_registration_current_and_usable(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        recorded = _stored(config)
        wire.delete_forgets = True
        wire.delete_agent_status = wire.delete_tool_status = 200
        wire.next_tool_ids = ["tool_alarm"]
        wire.create_agent_status = 403
        with pytest.raises(ConvAIError, match="HTTP 403"):
            agent.connection((_ALARM,))
        assert _stored(config) == recorded
        wire.calls.clear()
        assert agent.connection((_CLOCK,)) == (SIGNED_URL, ("tool_clock",))
    assert [method for method, _, _ in wire.calls] == ["GET"]


def test_a_failed_second_tool_create_deletes_the_first(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _REGISTRATION
    created: list[str] = []
    real = ConvAIControl.create_tool

    def fail_second(control: ConvAIControl, tool: ClientTool, marker: str) -> str:
        if created:
            msg = "ElevenLabs POST /tools failed (ReadTimeout)"
            raise ConvAIError(msg)
        created.append(real(control, tool, marker))
        return created[0]

    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        before = dict(wire.tools)
        wire.delete_forgets = True
        wire.delete_tool_status = 200
        wire.next_tool_ids = ["tool_alarm"]
        monkeypatch.setattr(ConvAIControl, "create_tool", fail_second)
        with pytest.raises(ConvAIError, match="ReadTimeout"):
            agent.connection((_ALARM, ClientTool("bell", "Ring twice")))
    assert wire.tools == before


def test_a_failed_first_provisioning_leaves_no_registration_and_no_resources(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.delete_forgets = True
    wire.delete_tool_status = 200
    wire.create_agent_status = 403
    with (
        ConvAIControl(API_KEY, base_url=wire.url) as control,
        pytest.raises(ConvAIError, match="HTTP 403"),
    ):
        VoiceAgent(control, path=config).connection((_CLOCK,))
    assert wire.tools == {}
    assert not config.exists()


def test_a_tool_that_could_not_be_deleted_is_kept_for_the_background_retirement(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.delete_tool_status = 403
    wire.create_agent_status = 403
    with (
        ConvAIControl(API_KEY, base_url=wire.url) as control,
        pytest.raises(ConvAIError, match="HTTP 403"),
    ):
        VoiceAgent(control, path=config).connection((_CLOCK,))
    stored = _stored(config)
    assert stored["retired_tools"] == ["tool_clock"]
    assert stored["pending_tools"] == []


# -- Invariant 2: intent is recorded first; a dead process is cleaned up ------


def test_the_intent_is_on_disk_before_the_first_remote_create(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _REGISTRATION
    seen: list[dict[str, object]] = []
    real = ConvAIControl.create_tool

    def observe(control: ConvAIControl, tool: ClientTool, marker: str) -> str:
        seen.append(_stored(config))
        return real(control, tool, marker)

    monkeypatch.setattr(ConvAIControl, "create_tool", observe)
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        VoiceAgent(control, path=config).connection((_CLOCK,))
    assert seen[0]["credential_id"] == control.credential_id
    assert seen[0]["pending_tools"] == []


def test_created_ids_are_recorded_as_pending_as_they_appear(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _REGISTRATION
    seen: list[dict[str, object]] = []
    real = ConvAIControl.create_agent

    def observe(control: ConvAIControl, tool_ids: tuple[str, ...], marker: str) -> str:
        seen.append(_stored(config))
        return real(control, tool_ids, marker)

    monkeypatch.setattr(ConvAIControl, "create_agent", observe)
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        VoiceAgent(control, path=config).connection((_CLOCK,))
    assert seen[0]["pending_tools"] == ["tool_clock"]
    finished = _stored(config)
    assert finished["pending_tools"] == []
    assert finished["pending_agents"] == []
    assert finished["tool_ids"] == ["tool_clock"]
    assert finished["agent_id"] == "agent_vox"


def test_an_interrupted_replacement_is_cleaned_up_by_the_next_retirement(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _REGISTRATION

    def die(control: ConvAIControl, tool_ids: tuple[str, ...], marker: str) -> str:
        raise Died

    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.delete_forgets = True
        wire.delete_agent_status = wire.delete_tool_status = 200
        wire.next_tool_ids = ["tool_alarm"]
        monkeypatch.setattr(ConvAIControl, "create_agent", die)
        with pytest.raises(Died):
            agent.connection((_ALARM,))
        assert _stored(config)["pending_tools"] == ["tool_alarm"]
        assert "tool_alarm" in wire.tools
        # The next voxd start: a fresh object, the same background retirement.
        VoiceAgent(control, path=config).retire_stale()
    assert "tool_alarm" not in wire.tools
    assert "tool_clock" in wire.tools
    assert _stored(config)["pending_tools"] == []
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        assert VoiceAgent(control, path=config).connection((_CLOCK,))[1] == (
            "tool_clock",
        )


def test_an_interrupted_first_provisioning_is_cleaned_up_too(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _REGISTRATION

    def die(control: ConvAIControl, tool_ids: tuple[str, ...], marker: str) -> str:
        raise Died

    wire.delete_forgets = True
    wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        monkeypatch.setattr(ConvAIControl, "create_agent", die)
        with pytest.raises(Died):
            VoiceAgent(control, path=config).connection((_CLOCK,))
        VoiceAgent(control, path=config).retire_stale()
    assert wire.tools == {}


def test_cleanup_of_an_interrupted_run_never_raises_and_keeps_what_it_could_not_delete(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.tools["tool_alarm"] = wire.tools["tool_clock"]
        record = _stored(config)
        record["pending_tools"] = ["tool_alarm"]
        config.write_text(json.dumps(record))
        wire.delete_tool_status = 403
        agent.retire_stale()
    assert _stored(config)["pending_tools"] == ["tool_alarm"]


def test_a_failing_cleanup_never_replaces_the_original_error(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        msg = "disk full"
        raise OSError(msg)

    wire.create_agent_status = 403
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=tmp_path / _REGISTRATION)
        monkeypatch.setattr(Disposal, "tools", broken)
        with pytest.raises(ConvAIError, match="HTTP 403") as caught:
            agent.connection((_CLOCK,))
    assert any("disk full" in note for note in caught.value.__notes__)


# -- The host marker file ------------------------------------------------------


def test_a_host_marker_that_cannot_be_written_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = HostMarker(tmp_path / _REGISTRATION)

    def fail(_fd: int, _data: bytes) -> int:
        msg = "no space left"
        raise OSError(msg)

    monkeypatch.setattr("os.write", fail)
    with pytest.raises(ConvAIError, match="host marker"):
        marker.text()
    assert not list(tmp_path.glob("*.host*"))
    monkeypatch.undo()
    assert marker.text().startswith("[vox-voice-agent host=")


def test_the_host_marker_token_is_stable_once_created(tmp_path: Path) -> None:
    config = tmp_path / _REGISTRATION
    assert HostMarker(config).text() == HostMarker(config).text()
