"""A create whose reply is lost leaves no marked resource behind on the account."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.voice_agent import ClientTool, ConvAIControl, ConvAIError, VoiceAgent
from punt_vox.voxd.voice_agent.host_marker import HostMarker
from punt_vox.voxd.voice_agent.replacement import SWEEP_BUDGET_S

from .fake_wire import API_KEY, SIGNED_URL

if TYPE_CHECKING:
    from pathlib import Path

    from .fake_wire import Requests

_CLOCK = ClientTool("clock", "Return UTC time")
_ALARM = ClientTool("alarm", "Ring a bell")
_BELL = ClientTool("bell", "Ring twice")
_REGISTRATION = "voice-agent.json"
_OTHER_HOST = "[vox-voice-agent host=00000000000000000000000000000000]"
_UNRECORDED = "check the ElevenLabs workspace for an unrecorded resource"


def _stored(path: Path) -> dict[str, object]:
    record: dict[str, object] = json.loads(path.read_text())
    return record


def _provision_clock(
    control: ConvAIControl, config: Path, wire: Requests
) -> VoiceAgent:
    agent = VoiceAgent(control, path=config)
    assert agent.connection((_CLOCK,)) == (SIGNED_URL, ("tool_clock",))
    wire.calls.clear()
    wire.listings.clear()
    wire.delete_forgets = True
    wire.delete_agent_status = wire.delete_tool_status = 200
    return agent


def test_a_tool_create_that_commits_and_loses_its_reply_is_deleted(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        before = dict(wire.tools), dict(wire.agents)
        recorded = _stored(config)
        wire.next_tool_ids = ["tool_alarm"]
        wire.drop_reply_after_tool_commit = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert (wire.tools, wire.agents) == before
    assert _stored(config) == recorded


def test_an_agent_create_that_commits_and_loses_its_reply_is_deleted_with_its_tools(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        before = dict(wire.tools), dict(wire.agents)
        wire.next_tool_ids = ["tool_alarm", "tool_bell"]
        wire.next_agent_ids = ["agent_new"]
        wire.drop_reply_after_agent_commit = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM, _BELL))
    assert (wire.tools, wire.agents) == before


def test_the_sweep_follows_the_listing_to_its_last_page(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.list_page_size = 1
        wire.next_tool_ids = ["tool_alarm"]
        wire.drop_reply_after_tool_commit = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert "tool_alarm" not in wire.tools
    assert wire.listings.count("tools") >= 2  # One tool per page, so more than one.


def test_a_foreign_resource_and_another_hosts_resource_are_never_deleted(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.tools["tool_foreign"] = "Someone else's tool"
        wire.tools["tool_other_host"] = f"Return UTC time {_OTHER_HOST}"
        wire.agents["agent_foreign"] = "Someone else's agent"
        wire.agents["agent_other_host"] = f"Vox {_OTHER_HOST}"
        wire.next_tool_ids = ["tool_alarm"]
        wire.drop_reply_after_tool_commit = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert "tool_alarm" not in wire.tools
    assert {"tool_foreign", "tool_other_host"} <= wire.tools.keys()
    assert {"agent_foreign", "agent_other_host"} <= wire.agents.keys()
    assert wire.deletes() == ["/v1/convai/tools/tool_alarm?force=true"]


def test_the_old_registrations_resources_survive_the_sweep(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.next_tool_ids = ["tool_alarm"]
        wire.drop_reply_after_tool_commit = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
        wire.calls.clear()
        assert agent.connection((_CLOCK,)) == (SIGNED_URL, ("tool_clock",))
    assert "tool_clock" in wire.tools
    assert "agent_vox" in wire.agents
    assert all("tool_clock" not in path for path in wire.deletes())


def test_a_failed_listing_leaves_the_create_error_intact_and_the_call_unblocked(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        recorded = _stored(config)
        wire.next_tool_ids = ["tool_alarm"]
        wire.drop_reply_after_tool_commit = True
        wire.list_status = 500
        with pytest.raises(ConvAIError, match=_UNRECORDED) as caught:
            agent.connection((_ALARM,))
        assert any("HTTP 500" in note for note in caught.value.__notes__)
        assert _stored(config) == recorded
        wire.calls.clear()
        assert agent.connection((_CLOCK,)) == (SIGNED_URL, ("tool_clock",))


def test_a_swept_resource_that_cannot_be_deleted_is_kept_for_retirement(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.delete_tool_status = 403
        wire.next_tool_ids = ["tool_alarm"]
        wire.drop_reply_after_tool_commit = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
        assert _stored(config)["retired_tools"] == ["tool_alarm"]
        wire.delete_tool_status = 200
        agent.retire_stale()
    assert "tool_alarm" not in wire.tools
    assert _stored(config)["retired_tools"] == []


def test_a_failed_first_provisioning_with_a_lost_reply_leaves_no_marked_resource(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.delete_forgets = True
    wire.delete_tool_status = 200
    wire.drop_reply_after_tool_commit = True
    with (
        ConvAIControl(API_KEY, base_url=wire.url) as control,
        pytest.raises(ConvAIError, match=_UNRECORDED),
    ):
        VoiceAgent(control, path=config).connection((_CLOCK,))
    assert wire.tools == {}
    assert not config.exists()


def test_the_sweep_runs_only_after_an_unknown_outcome(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.next_tool_ids = ["tool_alarm"]
        wire.create_agent_status = 403
        with pytest.raises(ConvAIError, match="HTTP 403"):
            agent.connection((_ALARM,))
    assert wire.listings == []
    assert HostMarker(config).text().startswith("[vox-voice-agent host=")


def test_a_failed_listing_on_first_provisioning_still_reports_and_cleans_up(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    wire.drop_reply_after_tool_commit = True
    wire.list_status = 500
    with (
        ConvAIControl(API_KEY, base_url=wire.url) as control,
        pytest.raises(ConvAIError, match=_UNRECORDED) as caught,
    ):
        VoiceAgent(control, path=config).connection((_CLOCK,))
    assert any("HTTP 500" in note for note in caught.value.__notes__)
    assert not config.exists()


@pytest.mark.parametrize("status", [500, 504, 408])
def test_a_server_error_after_a_tool_commit_is_swept_and_says_so(
    tmp_path: Path, wire: Requests, status: int
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        before = dict(wire.tools), dict(wire.agents)
        wire.next_tool_ids = ["tool_alarm"]
        wire.create_tool_status = status
        wire.commit_failed_create = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert (wire.tools, wire.agents) == before


def test_a_server_error_after_an_agent_commit_is_swept_with_its_tools(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        before = dict(wire.tools), dict(wire.agents)
        wire.next_tool_ids = ["tool_alarm"]
        wire.next_agent_ids = ["agent_new"]
        wire.create_agent_status = 504
        wire.commit_failed_create = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert (wire.tools, wire.agents) == before


def test_a_client_error_on_create_is_not_an_unknown_outcome(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        wire.next_tool_ids = ["tool_alarm"]
        wire.create_agent_status = 422
        with pytest.raises(ConvAIError, match="HTTP 422") as caught:
            agent.connection((_ALARM,))
        assert _UNRECORDED not in str(caught.value)
    assert wire.listings == []


def _lost_agent_reply(wire: Requests) -> None:
    """Arrange an agent create that commits, then loses its reply."""
    wire.next_tool_ids = ["tool_alarm"]
    wire.next_agent_ids = ["agent_new"]
    wire.drop_reply_after_agent_commit = True


def test_a_tools_listing_failure_keeps_the_agents_already_found(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        _lost_agent_reply(wire)
        wire.failing_listings = {"tools"}
        with pytest.raises(ConvAIError, match=_UNRECORDED) as caught:
            agent.connection((_ALARM,))
    assert "agent_new" not in wire.agents
    assert any("/tools" in note for note in caught.value.__notes__)


def test_both_listing_failures_are_attached(tmp_path: Path, wire: Requests) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        _lost_agent_reply(wire)
        wire.failing_listings = {"agents", "tools"}
        with pytest.raises(ConvAIError, match=_UNRECORDED) as caught:
            agent.connection((_ALARM,))
    notes = "\n".join(caught.value.__notes__)
    assert "GET /agents" in notes
    assert "GET /tools" in notes


def test_a_found_agent_that_cannot_be_deleted_is_named_when_the_tools_listing_fails(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        _lost_agent_reply(wire)
        wire.failing_listings = {"tools"}
        wire.delete_agent_status = 403
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert _stored(config)["retired_agents"] == ["agent_new"]


def test_ids_left_on_elevenlabs_are_named_when_the_registration_cannot_be_restored(
    tmp_path: Path, wire: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / _REGISTRATION
    original_replace = AtomicFile.replace

    def fail_restore(file: AtomicFile, text: str, *, mode: int | None = None) -> None:
        if json.loads(text)["retired_tools"]:
            msg = "state directory no longer writable"
            raise PermissionError(msg)
        original_replace(file, text, mode=mode)

    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = _provision_clock(control, config, wire)
        monkeypatch.setattr(AtomicFile, "replace", fail_restore)
        wire.next_tool_ids = ["tool_alarm"]
        wire.drop_reply_after_tool_commit = True
        wire.delete_tool_status = 403
        with pytest.raises(ConvAIError, match=_UNRECORDED) as caught:
            agent.connection((_ALARM,))
    notes = "\n".join(caught.value.__notes__)
    assert "tool_alarm" in notes
    assert "not restored" in notes


class _Clock:
    """A clock that advances *step* seconds each time it is read."""

    def __init__(self, step: float) -> None:
        self._now, self._step = 0.0, step

    def __call__(self) -> float:
        now = self._now
        self._now += self._step
        return now


def _slow_sweep_account(wire: Requests) -> None:
    """Leave a marked orphan on page one and the lost create on a later page."""
    wire.tools["tool_orphan"] = wire.tools["tool_clock"]
    for number in range(3):
        wire.tools[f"tool_foreign_{number}"] = "Someone else's tool"
    wire.list_page_size = 2
    wire.next_tool_ids = ["tool_alarm"]
    wire.drop_reply_after_tool_commit = True


def test_a_sweep_stops_at_its_deadline_and_still_deletes_what_it_found(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config, clock=_Clock(SWEEP_BUDGET_S * 0.6))
        agent.connection((_CLOCK,))
        wire.calls.clear()
        wire.delete_forgets = True
        wire.delete_agent_status = wire.delete_tool_status = 200
        _slow_sweep_account(wire)
        with pytest.raises(ConvAIError, match=_UNRECORDED) as caught:
            agent.connection((_ALARM,))
    assert "tool_orphan" not in wire.tools
    assert wire.listings.count("tools") == 2  # The deadline cut the walk short.
    assert any("did not finish within" in note for note in caught.value.__notes__)


def test_a_found_resource_the_deadline_sweep_cannot_delete_is_kept(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config, clock=_Clock(SWEEP_BUDGET_S * 0.6))
        agent.connection((_CLOCK,))
        wire.delete_tool_status = 403
        _slow_sweep_account(wire)
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert _stored(config)["retired_tools"] == ["tool_orphan"]


def test_a_sweep_within_its_deadline_reads_every_page(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config, clock=_Clock(0.0))
        agent.connection((_CLOCK,))
        wire.delete_forgets = True
        wire.delete_agent_status = wire.delete_tool_status = 200
        _slow_sweep_account(wire)
        with pytest.raises(ConvAIError, match=_UNRECORDED) as caught:
            agent.connection((_ALARM,))
    assert "tool_alarm" not in wire.tools
    notes = getattr(caught.value, "__notes__", [])
    assert not any("did not finish" in note for note in notes)


def _crowded_account(wire: Requests, kind: str) -> None:
    """Fill the *kind* listing with foreign entries, so it spans several pages."""
    held = wire.agents if kind == "agents" else wire.tools
    for number in range(4):
        held[f"{kind}_foreign_{number}"] = "Someone else's resource"
    wire.list_page_size = 2


def test_a_lost_tool_reply_sweeps_tools_before_the_agent_listing_spends_the_budget(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config, clock=_Clock(SWEEP_BUDGET_S * 0.5))
        agent.connection((_CLOCK,))
        wire.delete_forgets = True
        wire.delete_agent_status = wire.delete_tool_status = 200
        _crowded_account(wire, "agents")
        wire.next_tool_ids = ["tool_alarm"]
        wire.create_tool_status = 500
        wire.commit_failed_create = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert "tool_alarm" not in wire.tools


def test_a_lost_agent_reply_sweeps_the_agent_before_the_tool_listing_spends_the_budget(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config, clock=_Clock(SWEEP_BUDGET_S * 0.5))
        agent.connection((_CLOCK,))
        wire.delete_forgets = True
        wire.delete_agent_status = wire.delete_tool_status = 200
        _crowded_account(wire, "tools")
        wire.next_tool_ids = ["tool_alarm"]
        wire.next_agent_ids = ["agent_new"]
        wire.create_agent_status = 504
        wire.commit_failed_create = True
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert "agent_new" not in wire.agents
    assert "tool_alarm" not in wire.tools


def test_an_exhausted_budget_still_lets_each_listing_read_its_first_page(
    tmp_path: Path, wire: Requests
) -> None:
    config = tmp_path / _REGISTRATION
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        agent = VoiceAgent(control, path=config, clock=_Clock(SWEEP_BUDGET_S * 10))
        agent.connection((_CLOCK,))
        wire.listings.clear()
        wire.next_tool_ids = ["tool_alarm"]
        wire.create_tool_status = 500
        with pytest.raises(ConvAIError, match=_UNRECORDED):
            agent.connection((_ALARM,))
    assert sorted(wire.listings) == ["agents", "tools"]
