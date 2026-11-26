from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.voxd.voice_agent.agent_body import AgentBody
from punt_vox.voxd.voice_agent.host_zone import HostZone
from punt_vox.voxd.voice_agent.settings import AgentSettings

if TYPE_CHECKING:
    from pathlib import Path


def _settings(
    prompt: str = "You are Vox.", zone: str = "America/Los_Angeles"
) -> AgentSettings:
    return AgentSettings(prompt, timezone=zone)


def _body(
    settings: AgentSettings, tool_ids: tuple[str, ...] = ("t1",)
) -> dict[str, object]:
    return cast("dict[str, object]", AgentBody.create(settings, tool_ids))


def _at(node: object, *path: str) -> object:
    if not path:
        return node
    return _at(cast("dict[str, object]", node)[path[0]], *path[1:])


def test_the_whole_agent_is_defined_by_one_create_body() -> None:
    body = _body(_settings())
    assert (
        _at(body, "conversation_config", "agent", "prompt", "prompt") == "You are Vox."
    )
    assert _at(body, "conversation_config", "agent", "prompt", "tool_ids") == ["t1"]
    assert _at(body, "conversation_config", "agent", "prompt", "llm") == (
        "deepseek-v41-flash"
    )
    assert _at(body, "conversation_config", "agent", "prompt", "timezone") == (
        "America/Los_Angeles"
    )
    assert _at(body, "conversation_config", "tts", "voice_id") == (
        "cjVigY5qzO86Huf0OWal"
    )
    assert _at(body, "conversation_config", "agent", "first_message") == (
        "Hi, it's Vox. What are we working on?"
    )


def test_the_agent_waits_out_silence_instead_of_asking_are_you_there() -> None:
    body = _body(_settings())
    assert _at(body, "conversation_config", "turn") == {
        "turn_timeout": -1,
        "turn_eagerness": "patient",
    }
    skip = cast(
        "dict[str, object]",
        _at(
            body,
            "conversation_config",
            "agent",
            "prompt",
            "built_in_tools",
            "skip_turn",
        ),
    )
    assert skip["params"] == {"system_tool_type": "skip_turn", "wait_timeout_secs": -1}


def test_end_call_stays_a_built_in_tool() -> None:
    body = _body(_settings())
    tools = cast(
        "dict[str, object]",
        _at(body, "conversation_config", "agent", "prompt", "built_in_tools"),
    )
    assert set(tools) == {"end_call", "skip_turn"}


def test_the_call_is_not_recorded() -> None:
    body = _body(_settings())
    assert _at(body, "platform_settings", "privacy") == {"record_voice": False}


def test_the_body_carries_none_of_the_fields_the_server_derives() -> None:
    text = json.dumps(_body(_settings()))
    for derived in ("tool_ids_strict_default", '"tools"', 'native_mcp_server_ids": []'):
        assert derived not in text


def test_a_call_may_override_only_prompt_tools_and_first_message() -> None:
    body = _body(_settings())
    allowed = _at(
        body,
        "platform_settings",
        "overrides",
        "conversation_config_override",
        "agent",
    )
    assert allowed == {
        "prompt": {"prompt": True, "tool_ids": True, "tool_ids_strict": True},
        "first_message": True,
    }


def test_a_new_prompt_is_a_new_fingerprint_and_a_new_zone_is_not() -> None:
    assert AgentBody.fingerprint(_settings("a"), ()) != AgentBody.fingerprint(
        _settings("b"), ()
    )
    assert AgentBody.fingerprint(_settings(zone="Asia/Tokyo"), ()) == (
        AgentBody.fingerprint(_settings(zone="Europe/Paris"), ())
    )


def test_the_host_zone_is_the_name_the_localtime_link_points_at(tmp_path: Path) -> None:
    zones = tmp_path / "usr" / "share" / "zoneinfo" / "America"
    zones.mkdir(parents=True)
    target = zones / "Los_Angeles"
    target.write_bytes(b"")
    link = tmp_path / "localtime"
    link.symlink_to(target)
    assert HostZone(link, {}).name() == "America/Los_Angeles"


def test_the_host_zone_falls_back_to_the_tz_variable_then_utc(tmp_path: Path) -> None:
    missing = tmp_path / "nothing"
    assert HostZone(missing, {"TZ": "Europe/Paris"}).name() == "Europe/Paris"
    assert HostZone(missing, {"TZ": "PDT"}).name() == "UTC"
    assert HostZone(missing, {}).name() == "UTC"


@pytest.mark.parametrize("zone", ["PDT", "", "Not/AZone"])
def test_a_zone_that_is_not_an_iana_name_is_refused(zone: str) -> None:
    with pytest.raises(ValueError, match="IANA"):
        AgentSettings("p", timezone=zone)


def test_the_research_tools_get_a_deadline_longer_than_the_lookup_budget() -> None:
    from punt_vox.voxd.voice_agent.contracts import ClientTool
    from punt_vox.voxd.voice_call.lookup.tools import ResearchTools

    slow = ClientTool("search_web", "d", {"type": "object"}, response_timeout_secs=30)
    config = cast("dict[str, dict[str, object]]", AgentBody.tool(slow))
    assert config["tool_config"]["response_timeout_secs"] == 30
    assert ResearchTools.BUDGET_SECONDS < 30


def test_the_boss_catalog_gives_only_the_research_tools_a_longer_deadline() -> None:
    from punt_vox.voxd.voice_call.boss_tools import BossTools

    deadlines = {
        tool.name: tool.response_timeout_secs
        for tool in BossTools.CATALOG
        if tool.response_timeout_secs is not None
    }
    assert deadlines == {"search_web": 30, "read_docs": 30}


def test_a_tool_without_a_deadline_sends_none_to_the_provider() -> None:
    from punt_vox.voxd.voice_agent.contracts import ClientTool

    config = cast("dict[str, dict[str, object]]", AgentBody.tool(ClientTool("a", "d")))
    assert "response_timeout_secs" not in config["tool_config"]
