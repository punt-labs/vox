from __future__ import annotations

import pytest

from punt_vox.voxd.voice_agent import ConvAIError
from punt_vox.voxd.voice_agent._decode import EventDecoder, Message
from punt_vox.voxd.voice_agent.events import AgentToolResponse


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        ([], "event must be a JSON object"),
        ({}, "requires object agent_tool_response"),
        ({"agent_tool_response": {"tool_call_id": 9}}, "requires string tool_call_id"),
        (
            {"agent_tool_response": {"tool_call_id": "c", "is_error": "false"}},
            "requires boolean is_error",
        ),
    ],
    ids=["not-an-object", "no-payload", "bad-call-id", "bad-error-flag"],
)
def test_tool_ack_rejects_invalid_shape(body: object, diagnostic: str) -> None:
    with pytest.raises(ConvAIError, match=diagnostic):
        EventDecoder.decode("agent_tool_response", Message(body))


def _ack(**fields: object) -> object:
    return EventDecoder.decode(
        "agent_tool_response",
        Message(
            {
                "agent_tool_response": {
                    "tool_call_id": "end_1",
                    "is_error": False,
                    "is_called": True,
                    "event_id": 7,
                    **fields,
                }
            }
        ),
    )


def test_a_system_end_call_ack_is_the_agent_hanging_up() -> None:
    ack = _ack(tool_name="end_call", tool_type="system")
    assert ack == AgentToolResponse("end_1", False, "end_call", "system")
    assert isinstance(ack, AgentToolResponse)
    assert ack.ends_call


@pytest.mark.parametrize(
    "fields",
    [
        {"tool_name": "end_call", "tool_type": "system", "is_error": True},
        {"tool_name": "end_call", "tool_type": "system", "is_called": False},
        {"tool_name": "end_call", "tool_type": "client"},
        {"tool_name": "stop_worker", "tool_type": "client"},
        {},
    ],
    ids=[
        "failed-end-call",
        "skipped-end-call",
        "client-tool-named-end-call",
        "other-tool",
        "unnamed",
    ],
)
def test_any_other_ack_does_not_end_the_call(fields: dict[str, object]) -> None:
    ack = _ack(**fields)
    assert isinstance(ack, AgentToolResponse)
    assert not ack.ends_call


@pytest.mark.parametrize("field", ["tool_name", "tool_type"])
def test_an_ack_with_a_non_string_tool_field_is_rejected(field: str) -> None:
    with pytest.raises(ConvAIError, match=f"requires string {field}"):
        _ack(**{field: 3})


def test_an_ack_with_the_called_flag_absent_is_a_tool_that_ran() -> None:
    """An absent is_called reads as true: only an explicit false withholds it.

    The field is optional in the provider's event; parsing the boundary
    supplies the default so that an acknowledgement without it still means
    what the provider means by sending one, a tool that ran.
    """
    ack = EventDecoder.decode(
        "agent_tool_response",
        Message(
            {
                "agent_tool_response": {
                    "tool_call_id": "end_1",
                    "is_error": False,
                    "tool_name": "end_call",
                    "tool_type": "system",
                }
            }
        ),
    )
    assert ack == AgentToolResponse("end_1", False, "end_call", "system")
    assert isinstance(ack, AgentToolResponse)
    assert ack.is_called
    assert ack.ends_call


def test_a_skipped_end_call_decodes_as_not_called() -> None:
    ack = _ack(tool_name="end_call", tool_type="system", is_called=False)
    assert ack == AgentToolResponse(
        "end_1", False, "end_call", "system", is_called=False
    )


def test_an_ack_with_a_non_boolean_called_flag_is_rejected() -> None:
    with pytest.raises(ConvAIError, match="requires boolean is_called"):
        _ack(tool_name="end_call", tool_type="system", is_called="yes")
