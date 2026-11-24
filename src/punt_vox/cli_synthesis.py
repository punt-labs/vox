"""Prepare segments and synthesis settings for the speech and recording CLIs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, TypedDict, Unpack, final

import typer

from punt_vox.cli_guard import CliGuard
from punt_vox.cli_io import TextInput
from punt_vox.session_spec import SessionSpec
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.types_synthesis_errors import (
    ModelNotAvailableError,
    ProviderNotConfiguredError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.output_formatter import OutputFormatter


class _SynthesisOptions(TypedDict):
    voice: str | None
    language: str | None
    rate: int
    provider: str | None
    model: str | None
    stability: float | None
    similarity: float | None
    style: float | None


@final
class CliSynthesis:
    """Translate CLI input and repo defaults into requests ready for voxd."""

    __slots__ = ("_guard", "_text_input")
    _guard: CliGuard
    _text_input: TextInput

    def __new__(cls, formatter: OutputFormatter) -> Self:
        """Create a synthesis boundary using the command's formatter."""
        self = super().__new__(cls)
        self._guard = CliGuard(formatter)
        self._text_input = TextInput(formatter)
        return self

    def prepare(
        self,
        text: str | None,
        from_file: Path | None,
        *,
        speaker_boost: bool,
        api_key: str | None = None,
        **options: Unpack[_SynthesisOptions],
    ) -> tuple[list[str], SynthesisSpec]:
        """Resolve segments first, then validate options and fill repo defaults."""
        segments = self._text_input.resolve(text, from_file)
        spec = SynthesisSpec(
            speaker_boost=speaker_boost or None,
            api_key=api_key,
            **options,
        )
        try:
            spec.validate()
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        return segments, self._fill_from_state(spec)

    def _fill_from_state(self, spec: SynthesisSpec) -> SynthesisSpec:
        """Resolve defaults and report provider or model errors in CLI format."""
        try:
            return SessionSpec.for_repo().fill(spec)
        except ProviderNotConfiguredError:
            self._guard.fail(str(ProviderNotConfiguredError.for_cli()))
        except ModelNotAvailableError as exc:
            self._guard.fail(str(exc))
