"""Typer option annotations shared by the speech and recording commands."""

from __future__ import annotations

from typing import Annotated

import typer

__all__ = ["LanguageOpt", "RateOpt"]

LanguageOpt = Annotated[
    str | None,  # Omitting --language leaves provider language selection unchanged.
    typer.Option("--language", "--lang", help="ISO 639-1 language code (e.g. de, ko)."),
]
RateOpt = Annotated[
    int,
    typer.Option(
        "--rate", help="Positive speech rate percentage (e.g. 90 = 90% speed)."
    ),
]
