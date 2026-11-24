"""Tests for the shared Typer option annotations."""

from __future__ import annotations

from typing import get_args

import pytest
from _cli_introspect import command_opts, option_help
from typer.models import OptionInfo

from punt_vox.types_cli_options import LanguageOpt, RateOpt


@pytest.mark.parametrize(
    ("alias", "value_type", "flags"),
    [
        (LanguageOpt, str | None, {"--language", "--lang"}),
        (RateOpt, int, {"--rate"}),
    ],
)
def test_synthesis_option_annotations(
    alias: object, value_type: object, flags: set[str]
) -> None:
    annotation, option = get_args(alias)
    assert annotation == value_type
    assert isinstance(option, OptionInfo)
    assert option.help
    assert flags <= command_opts("say")
    assert flags <= command_opts("rec", "new")


@pytest.mark.parametrize(
    "flag",
    [
        "--from",
        "--language",
        "--lang",
        "--model",
        "--provider",
        "--rate",
        "--similarity",
        "--speaker-boost",
        "--stability",
        "--style",
        "--voice",
    ],
)
def test_say_and_rec_new_share_synthesis_help(flag: str) -> None:
    assert option_help("rec", "new", option=flag) == option_help("say", option=flag)
