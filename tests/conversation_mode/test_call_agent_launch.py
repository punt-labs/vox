"""Tests for :class:`CallAgentLaunch`: the call agent's argv and environment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from punt_vox.providers.credentials import PROVIDER_KEY_NAMES
from punt_vox.voxd.conversation_mode import call_agent_launch as launch_module
from punt_vox.voxd.conversation_mode.call_agent_launch import (
    READ_ONLY_TOOLS,
    SECRET_GUARD,
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
            "--no-context-files",
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


_GUARD_VARS = {"VOX_CALL_GUARD_ROOT", "VOX_CALL_GUARD_PATTERNS"}


def _passed(launch: CallAgentLaunch, parent: dict[str, str]) -> set[str]:
    """Return the names of *parent* the child receives, guard config aside."""
    return set(launch.env(parent)) - _GUARD_VARS


class TestEnvironment:
    """The child environment is an allowlist: nothing secret rides along by default."""

    def test_voxd_provider_keys_never_reach_the_child(
        self, launch: CallAgentLaunch
    ) -> None:
        assert _passed(launch, dict.fromkeys(PROVIDER_KEY_NAMES, "value")) == set()

    @pytest.mark.parametrize(
        "name",
        [
            "GITHUB_PERSONAL_ACCESS_TOKEN",
            "BEADS_DOLT_PASSWORD",
            "RESEND_API_KEY",
            "CONTEXT7_API_KEY",
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "FOO_API_KEY",
            "GH_TOKEN",
            "SSH_AUTH_SOCK",
        ],
    )
    def test_an_unlisted_variable_never_reaches_the_child(
        self, launch: CallAgentLaunch, name: str
    ) -> None:
        assert name not in launch.env({name: "secret", "PATH": "/usr/bin"})

    @pytest.mark.parametrize(
        "name",
        [
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "LANG",
            "TERM",
            "TMPDIR",
            "LC_ALL",
            "LC_CTYPE",
            "XDG_CONFIG_HOME",
            "XDG_RUNTIME_DIR",
            "PI_CODING_AGENT_DIR",
            "PI_OFFLINE",
        ],
    )
    def test_the_allowlisted_variables_pass_through(
        self, launch: CallAgentLaunch, name: str
    ) -> None:
        assert launch.env({name: "v"})[name] == "v"

    def test_a_prefix_is_not_matched_mid_name(self, launch: CallAgentLaunch) -> None:
        """``LC_`` and ``PI_`` are prefixes, not substrings anywhere in a name."""
        assert _passed(launch, {"XAPI_KEY": "s", "BLC_X": "s"}) == set()

    def test_the_guard_is_configured_from_the_policy(
        self, launch: CallAgentLaunch
    ) -> None:
        env = launch.env({})
        assert json.loads(env["VOX_CALL_GUARD_PATTERNS"]) == list(
            launch.policy.patterns
        )

    def test_a_parent_cannot_override_the_guard_config(
        self, launch: CallAgentLaunch, tmp_path: Path
    ) -> None:
        env = launch.env({"VOX_CALL_GUARD_ROOT": "/"})
        assert env["VOX_CALL_GUARD_ROOT"] == str(tmp_path.resolve())


class TestSecretGuard:
    def test_the_guard_extension_is_loaded_explicitly(
        self, launch: CallAgentLaunch
    ) -> None:
        assert _flag_value(launch.argv(), "--extension") == str(SECRET_GUARD)

    def test_the_guard_extension_ships_with_vox(self) -> None:
        assert SECRET_GUARD.is_file()

    def test_a_missing_guard_refuses_to_build_a_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(launch_module, "SECRET_GUARD", tmp_path / "gone.js")
        with pytest.raises(ValueError, match="secret guard is missing"):
            CallAgentLaunch(SecretPathPolicy(tmp_path))
