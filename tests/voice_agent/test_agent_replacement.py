"""A replacement agent that fails or is interrupted leaves no remote orphans."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.voice_agent import ClientTool, ConvAIControl, ConvAIError, VoiceAgent

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
    assert _stored(config)["retired_tools"] == []


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
    assert _stored(config)["retired_agents"] == []


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
        VoiceAgent(control, path=second).teardown()
    assert wire.deletes() == []
    assert not second.exists()


def test_a_tampered_registration_cannot_delete_a_foreign_resource_at_teardown(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.tools["tool_foreign"] = _FOREIGN_DESCRIPTION
    wire.delete_tool_status = 200
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        _write_registration(config, control, tool_ids=["tool_foreign"])
        VoiceAgent(control, path=config).teardown()
    assert wire.deletes() == []
    assert not config.exists()


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
