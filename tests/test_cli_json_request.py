"""JSON intent is readable without invoking commands or option callbacks."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from typer._coercion import TypeDescriptor
from typer.core import TyperCommand, TyperGroup, TyperOption
from typer.models import OptionInfo

from punt_vox.cli_json_request import JsonRequest


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], False),
        (["child"], False),
        (["--json", "child"], True),
        (["child", "--json"], True),
        (["nested", "child", "--json"], True),
        (["missing", "--json"], True),
        (["missing"], False),
        (["--verbose=true", "--json"], True),
        (["--json=false"], False),
        (["child", "--count", "invalid", "--json"], True),
        (["child", "--", "--json"], False),
    ],
)
def test_json_intent_precedes_callbacks(args: list[str], expected: bool) -> None:
    callback = Mock(side_effect=AssertionError("callback must not run"))
    child = TyperCommand(
        "child",
        callback=callback,
        params=[
            TyperOption(
                param_decls=["--count"],
                type_descriptor=TypeDescriptor(int, OptionInfo(), None),
                callback=callback,
            )
        ],
    )
    root = TyperGroup(
        name="root",
        callback=callback,
        params=[
            TyperOption(
                param_decls=["json_output", "--json"],
                type_descriptor=TypeDescriptor(bool, OptionInfo(), None),
                is_flag=True,
                callback=callback,
            ),
            TyperOption(
                param_decls=["--verbose"],
                type_descriptor=TypeDescriptor(bool, OptionInfo(), None),
                is_flag=True,
                callback=callback,
            ),
        ],
        commands={
            "child": child,
            "nested": TyperGroup(name="nested", commands={"child": child}),
        },
    )
    original = args.copy()
    with root.context_class(root) as context:
        assert JsonRequest(root).requested(context, args) is expected
    assert args == original
    callback.assert_not_called()
