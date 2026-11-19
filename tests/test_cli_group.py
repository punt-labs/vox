"""Usage errors retain Click's status and respect the CLI output mode."""

from __future__ import annotations

import json

import pytest
from typer.core import TyperGroup
from typer.testing import CliRunner

from punt_vox.__main__ import app


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["say", "hi", "--rate", "nope"],
            "Invalid value for '--rate': Input should be a valid integer",
        ),
        (
            ["say", "hi", "--once", "-1"],
            "Invalid value for '--once': Input should be greater than or equal to 0",
        ),
        (
            ["say", "hi", "--once", "nope"],
            "Invalid value for '--once': Input should be a valid integer",
        ),
        (["nonexistent"], "No such command 'nonexistent'."),
        (["--unknown"], "No such option: --unknown"),
        (
            ["--verbose=true", "nonexistent"],
            "Option '--verbose' does not take a value.",
        ),
        (["daemon", "nonexistent"], "No such command 'nonexistent'."),
    ],
)
@pytest.mark.parametrize("position", ["before", "after"])
def test_json_usage_errors(args: list[str], message: str, position: str) -> None:
    argv = ["--json", *args] if position == "before" else [*args, "--json"]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert set(payload) == {"error"}
    assert payload["error"].startswith(message)
    assert result.stderr == ""


@pytest.mark.parametrize(
    "args",
    [
        ["say", "hi", "--rate", "nope"],
        ["nonexistent"],
        ["daemon"],
        ["--verbose=true", "nonexistent"],
        ["--json=false", "nonexistent"],
        ["--json=", "nonexistent"],
    ],
)
def test_plain_usage_errors_unchanged(args: list[str]) -> None:
    runner = CliRunner()
    result = runner.invoke(app, args)
    original_cls = app.info.cls
    try:
        app.info.cls = TyperGroup
        original = runner.invoke(app, args)
    finally:
        app.info.cls = original_cls
    assert result.exit_code == original.exit_code == 2
    assert result.stdout == original.stdout
    assert result.stderr == original.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["nonexistent", "--", "--json"],
        ["--", "nonexistent", "--json"],
        ["daemon", "--", "nonexistent", "--json"],
        ["--verbose=true", "--", "nonexistent", "--json"],
    ],
)
def test_json_literal_after_separator_is_not_a_flag(args: list[str]) -> None:
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr
    test_plain_usage_errors_unchanged(args)


def test_json_missing_option_value() -> None:
    result = CliRunner().invoke(app, ["--json", "say", "hi", "--rate"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": "Option '--rate' requires an argument."
    }
    assert result.stderr == ""


@pytest.mark.parametrize(
    "args",
    [
        ["--verbose=true", "--json", "nonexistent"],
        ["--json=false", "--json", "nonexistent"],
        ["--json", "--json=false", "nonexistent"],
    ],
)
def test_valid_json_flag_survives_malformed_flag(args: list[str]) -> None:
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"].endswith("does not take a value.")
    assert result.stderr == ""


def test_usage_error_does_not_leak_output_mode() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--json", "nonexistent"]).exit_code == 2
    result = runner.invoke(app, ["nonexistent"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "No such command" in result.stderr


def test_json_group_without_subcommand() -> None:
    result = CliRunner().invoke(app, ["--json", "daemon"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"error": "Missing command for 'daemon'."}
    assert result.stderr == ""


def test_root_separator_keeps_child_json_option() -> None:
    result = CliRunner().invoke(app, ["--", "say", "hi", "--json", "--rate", "nope"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert set(payload) == {"error"}
    assert payload["error"].startswith("Invalid value for '--rate':")
    assert "valid integer" in payload["error"]
    assert result.stderr == ""


def test_global_json_option_on_command_without_local_flag() -> None:
    result = CliRunner().invoke(app, ["cache", "status", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"error": "No such option: --json"}
    assert result.stderr == ""


@pytest.mark.parametrize(
    "args",
    [
        ["say", "hi", "--voice", "--json", "--rate", "nope"],
        ["music", "new", "PROMPT", "--title", "--json", "--unknown"],
        ["say", "--rate", "nope", "--", "--json"],
        ["--verbose=true", "say", "hi", "--voice", "--json", "--rate", "nope"],
        ["say", "--verbose=true", "--rate", "nope", "--", "--json"],
    ],
)
def test_json_value_or_child_literal_preserves_plain_errors(args: list[str]) -> None:
    test_plain_usage_errors_unchanged(args)
    assert CliRunner().invoke(app, args).stdout == ""
