"""Read a ``vox call --script`` file into voxd's scripted-input wire form."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Required, Self, TypedDict, cast, final

import typer

__all__ = ["ScriptTurn", "VoiceCallScript"]


class ScriptTurn(TypedDict, total=False):
    """One turn of ``call_start``'s ``script`` field; voxd validates each field."""

    pcm_path: Required[str]
    pause_s: float


@final
class VoiceCallScript:
    """JSONL turns of recorded caller audio, one ``{"pcm_path", "pause_s"}`` per line.

    A relative ``pcm_path`` names a file beside the script, so a script and its
    recordings move together. voxd owns every limit (turn count, file size,
    pause range); this reader only resolves paths and reports malformed lines.
    """

    __slots__ = ("_turns",)
    _turns: tuple[ScriptTurn, ...]

    def __new__(cls, turns: tuple[ScriptTurn, ...]) -> Self:
        """Hold already-resolved wire turns."""
        self = super().__new__(cls)
        self._turns = turns
        return self

    @classmethod
    def read(cls, path: Path) -> Self:
        """Parse *path*, resolving each turn's ``pcm_path`` against its directory.

        Raises:
            typer.BadParameter: If the file is unreadable or a line is not a turn.
        """
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise typer.BadParameter(f"cannot read {path}: {exc}") from None
        base = path.resolve().parent
        turns = tuple(
            cls._turn(base, number, line)
            for number, line in enumerate(lines, start=1)
            if line.strip()
        )
        return cls(turns)

    @staticmethod
    def _turn(base: Path, number: int, line: str) -> ScriptTurn:
        """Return one wire turn with an absolute ``pcm_path``."""
        try:
            parsed: object = json.loads(line)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"line {number}: {exc.msg}") from None
        turn = cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}
        pcm_path = turn.get("pcm_path")
        if not isinstance(pcm_path, str):
            raise typer.BadParameter(f'line {number}: expected {{"pcm_path": ...}}')
        return cast("ScriptTurn", {**turn, "pcm_path": str(base / pcm_path)})

    def wire(self) -> list[ScriptTurn]:
        """Return the ``script`` field ``call_start`` sends to voxd."""
        return list(self._turns)
