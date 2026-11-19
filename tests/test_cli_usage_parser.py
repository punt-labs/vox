"""The output-intent parser preserves token roles without running user code."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from typer.core import TyperCommand, TyperOption

from punt_vox.cli_usage_parser import UsageParser


@pytest.fixture
def parser() -> UsageParser:
    callback = Mock(side_effect=AssertionError("Option callbacks must not run"))
    json_option = TyperOption(
        param_decls=["--json", "json_output"], is_flag=True, callback=callback
    )
    command = TyperCommand(
        "say",
        params=[
            json_option,
            TyperOption(param_decls=["--verbose"], is_flag=True, callback=callback),
            TyperOption(param_decls=["--voice"], callback=callback),
            TyperOption(param_decls=["--rate"], type=int, callback=callback),
        ],
    )
    context = command.context_class(command, ignore_unknown_options=True)
    return UsageParser(command, context, json_option)


@pytest.mark.parametrize(
    ("args", "requested", "rest"),
    [
        ([], False, []),
        (["--json"], True, []),
        (["--json=false"], False, []),
        (["--json=false", "--json"], True, []),
        (["--verbose=true", "--json"], True, []),
        (["--voice", "--json=false", "--verbose=true"], False, []),
        (["--voice", "--json", "--verbose=true"], False, []),
        (["--voice=--json", "--rate", "nope"], False, []),
        (["--json", "--rate", "nope"], True, []),
        (["--json", "--rate"], True, []),
        (["--rate"], False, []),
        (["--", "--json"], False, ["--json"]),
        (["--unknown", "--json"], True, ["--unknown"]),
    ],
)
def test_parse_output_intent(
    parser: UsageParser, args: list[str], requested: bool, rest: list[str]
) -> None:
    original = args.copy()
    assert parser.parse(args) == (requested, rest)
    assert args == original
