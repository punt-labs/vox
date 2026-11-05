"""Tests for :class:`CallAgentLaunch`: the call agent's argv and environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.providers.credentials import PROVIDER_KEY_NAMES
from punt_vox.voxd.conversation_mode.call_agent_launch import (
    READ_ONLY_TOOLS,
    CallAgentLaunch,
)
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy


@pytest.fixture
def launch(tmp_path: Path) -> CallAgentLaunch:
    return CallAgentLaunch(SecretPathPolicy(tmp_path), pi_bin="/opt/bin/pi")


def _flag_value(argv: tuple[str, ...], flag: str) -> str:
    return argv[argv.index(flag) + 1]


class TestReadOnly:
    def test_the_tool_allowlist_is_exactly_the_read_only_built_ins(
        self, launch: CallAgentLaunch
    ) -> None:
        assert _flag_value(launch.argv(), "--tools") == "read,grep,find,ls"

    @pytest.mark.parametrize("tool", ["bash", "edit", "write"])
    def test_no_mutating_tool_is_ever_allowed(self, tool: str) -> None:
        assert tool not in READ_ONLY_TOOLS

    @pytest.mark.parametrize(
        "flag",
        [
            "--no-session",
            "--no-extensions",
            "--no-mcp",
            "--no-skills",
            "--no-prompt-templates",
            "--no-approve",
        ],
    )
    def test_every_surface_widening_source_is_switched_off(
        self, launch: CallAgentLaunch, flag: str
    ) -> None:
        assert flag in launch.argv()

    def test_it_runs_in_rpc_mode_from_the_configured_binary(
        self, launch: CallAgentLaunch
    ) -> None:
        argv = launch.argv()
        assert (argv[0], _flag_value(argv, "--mode")) == ("/opt/bin/pi", "rpc")


class TestSystemPrompt:
    def test_the_deny_list_rides_the_system_prompt(
        self, launch: CallAgentLaunch
    ) -> None:
        prompt = _flag_value(launch.argv(), "--append-system-prompt")
        assert launch.policy.prompt_clause() in prompt


class TestEnvironment:
    def test_provider_keys_are_stripped(self) -> None:
        parent = {
            "ELEVENLABS_API_KEY": "el-secret",
            "OPENAI_API_KEY": "sk-secret",
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "AWS_SESSION_TOKEN": "aws-token",
            "AWS_PROFILE": "dev",
        }
        assert CallAgentLaunch.env(parent) == {}

    def test_every_key_voxd_loads_is_stripped(self) -> None:
        parent = dict.fromkeys(PROVIDER_KEY_NAMES, "value")
        assert CallAgentLaunch.env(parent) == {}

    def test_ordinary_variables_pass_through(self) -> None:
        parent = {"PATH": "/usr/bin", "HOME": "/home/x", "OPENAI_API_KEY": "sk"}
        assert CallAgentLaunch.env(parent) == {"PATH": "/usr/bin", "HOME": "/home/x"}
