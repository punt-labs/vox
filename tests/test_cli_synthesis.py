"""Tests for the shared CLI synthesis boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import typer

from punt_vox.cli_synthesis import CliSynthesis
from punt_vox.output_formatter import OutputFormatter

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("[", "invalid JSON"),
        ('["Hello"]', "stability must be between 0.0 and 1.0"),
    ],
)
def test_prepare_resolves_input_before_validating_options(
    tmp_path: Path, content: str, error: str
) -> None:
    source = tmp_path / "segments.json"
    source.write_text(content, encoding="utf-8")
    synthesis = CliSynthesis(OutputFormatter())

    with pytest.raises(typer.BadParameter, match=error):
        synthesis.prepare(
            None,
            source,
            voice=None,
            language=None,
            rate=90,
            provider=None,
            model=None,
            stability=2.0,
            similarity=None,
            style=None,
            speaker_boost=False,
        )
