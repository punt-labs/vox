"""The account listing is read strictly: a malformed page is an error, never a gap."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.voice_agent import ConvAIControl, ConvAIError

from .fake_wire import API_KEY

if TYPE_CHECKING:
    from .fake_wire import Requests

_MARKER = "[vox-voice-agent host=abc]"


def _never() -> bool:
    return False


def _marked_tools(wire: Requests) -> tuple[str, ...]:
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        return control.listing.marked_tools(_MARKER, _never)


@pytest.mark.parametrize("has_more", ["yes", None, 1, "false"])
def test_a_page_without_a_real_boolean_has_more_is_an_error(
    wire: Requests, has_more: object
) -> None:
    wire.tools["tool_a"] = f"Mine {_MARKER}"
    wire.has_more_override = has_more
    with pytest.raises(ConvAIError, match="has_more"):
        _marked_tools(wire)


def test_a_page_that_says_has_more_false_ends_the_walk(wire: Requests) -> None:
    wire.tools["tool_a"] = f"Mine {_MARKER}"
    wire.has_more_override = False
    assert _marked_tools(wire) == ("tool_a",)


def test_an_entry_that_is_not_an_object_is_an_error(wire: Requests) -> None:
    wire.extra_entries["tools"] = ["junk"]
    with pytest.raises(ConvAIError, match="not an object"):
        _marked_tools(wire)


def test_a_marked_entry_without_a_usable_id_is_an_error(wire: Requests) -> None:
    wire.extra_entries["tools"] = [{"tool_config": {"description": _MARKER}}]
    with pytest.raises(ConvAIError, match="no usable id"):
        _marked_tools(wire)


def test_a_marked_entry_with_an_invalid_id_is_an_error(wire: Requests) -> None:
    wire.extra_entries["tools"] = [
        {"id": "bad id/..", "tool_config": {"description": _MARKER}}
    ]
    with pytest.raises(ConvAIError, match="Invalid ElevenLabs resource ID"):
        _marked_tools(wire)


def test_an_entry_without_text_or_marker_never_aborts_the_walk(
    wire: Requests,
) -> None:
    wire.tools["tool_a"] = f"Mine {_MARKER}"
    wire.extra_entries["tools"] = [
        {"id": "no text"},  # No text: nothing to carry a marker.
        {"id": "odd id/..", "tool_config": {"description": "Foreign"}},
    ]
    assert _marked_tools(wire) == ("tool_a",)


def test_a_listing_longer_than_the_page_bound_names_the_pages_read(
    wire: Requests,
) -> None:
    wire.list_page_size = 1
    for number in range(101):
        wire.tools[f"tool_{number}"] = "Foreign"
    with pytest.raises(ConvAIError, match=r"did not end after 100 pages"):
        _marked_tools(wire)


def _marked_agents(wire: Requests) -> tuple[str, ...]:
    with ConvAIControl(API_KEY, base_url=wire.url) as control:
        return control.listing.marked_agents(_MARKER, _never)


def test_an_agent_whose_name_is_not_text_is_an_error(wire: Requests) -> None:
    wire.extra_entries["agents"] = [{"agent_id": "agent_x", "name": 7}]
    with pytest.raises(ConvAIError, match="not text"):
        _marked_agents(wire)


def test_a_tool_whose_description_is_not_text_is_an_error(wire: Requests) -> None:
    wire.extra_entries["tools"] = [{"id": "t", "tool_config": {"description": 7}}]
    with pytest.raises(ConvAIError, match="not text"):
        _marked_tools(wire)


@pytest.mark.parametrize("config", ["flat", 3, ["description"]])
def test_a_tool_config_that_is_not_an_object_is_an_error(
    wire: Requests, config: object
) -> None:
    wire.extra_entries["tools"] = [{"id": "t", "tool_config": config}]
    with pytest.raises(ConvAIError, match="not an object"):
        _marked_tools(wire)


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "t"},
        {"id": "t", "tool_config": None},
        {"id": "t", "tool_config": {}},
        {"id": "t", "tool_config": {"description": None}},
        {"id": "t", "tool_config": {"description": ""}},
    ],
)
def test_an_entry_with_no_text_is_still_skipped(
    wire: Requests, entry: dict[str, object]
) -> None:
    wire.extra_entries["tools"] = [entry]
    assert _marked_tools(wire) == ()
