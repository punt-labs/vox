"""Tests for :class:`CallAgentEnv`: the call agent's environment allowlist."""

from __future__ import annotations

import pytest

from punt_vox.voxd.conversation_mode.call_agent_env import CallAgentEnv


@pytest.mark.parametrize("name", ["PATH", "LC_ALL", "XDG_DATA_DIRS", "PI_OFFLINE"])
def test_listed_names_and_prefixes_are_admitted(name: str) -> None:
    assert CallAgentEnv.admits(name)


@pytest.mark.parametrize(
    "name", ["GITHUB_PERSONAL_ACCESS_TOKEN", "BEADS_DOLT_PASSWORD", "path", "XPI_X"]
)
def test_everything_else_is_refused(name: str) -> None:
    assert not CallAgentEnv.admits(name)


def test_allowlisted_keeps_values_unchanged() -> None:
    parent = {"HOME": "/home/x", "GH_TOKEN": "secret"}
    assert CallAgentEnv.allowlisted(parent) == {"HOME": "/home/x"}
