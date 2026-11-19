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
            "Invalid value for '--rate': 'nope' is not a valid int.",
        ),
        (["nonexistent"], "No such command 'nonexistent'."),
        (["--unknown"], "No such option: --unknown"),
        (["daemon", "nonexistent"], "No such command 'nonexistent'."),
    ],
)
@pytest.mark.parametrize("position", ["before", "after"])
def test_json_usage_errors(args: list[str], message: str, position: str) -> None:
    argv = ["--json", *args] if position == "before" else [*args, "--json"]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"error": message}
    assert result.stderr == ""


@pytest.mark.parametrize(
    "args", [["say", "hi", "--rate", "nope"], ["nonexistent"], ["daemon"]]
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


def test_json_literal_after_separator_is_not_a_flag() -> None:
    result = CliRunner().invoke(app, ["nonexistent", "--", "--json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "No such command" in result.stderr


def test_json_missing_option_value() -> None:
    result = CliRunner().invoke(app, ["--json", "say", "hi", "--rate"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": "Option '--rate' requires an argument."
    }
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
