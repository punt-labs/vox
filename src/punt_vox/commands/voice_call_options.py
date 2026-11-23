"""Start fields for a scripted or live voice call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self, final

import typer

from punt_vox.commands.voice_call_script import VoiceCallScript

if TYPE_CHECKING:
    from pathlib import Path


type EchoControl = Literal["headset", "aec"]


@final
class VoiceCallOptions:
    """A recording to play or the caller's declaration of echo protection."""

    __slots__ = ("_echo_control", "_script")
    _script: Path | None
    _echo_control: EchoControl | None

    def __new__(cls, script: Path | None, echo_control: EchoControl | None) -> Self:
        """Reject echo declarations for prerecorded input."""
        if script is not None and echo_control is not None:
            raise typer.BadParameter("--headset/--aec apply only to live calls")
        self = super().__new__(cls)
        self._script, self._echo_control = script, echo_control
        return self

    @classmethod
    def from_flags(cls, script: Path | None, *, headset: bool, aec: bool) -> Self:
        """Validate the CLI input choice before contacting the daemon."""
        if headset and aec:
            raise typer.BadParameter("--headset and --aec are mutually exclusive")
        echo_control: EchoControl | None = (
            "headset" if headset else "aec" if aec else None
        )
        return cls(script, echo_control)

    def wire(self) -> dict[str, object]:
        """Load the script if supplied and serialize the call start fields."""
        if self._script is not None:
            return {"script": VoiceCallScript.read(self._script).wire()}
        return {"echo_control": self._echo_control} if self._echo_control else {}
