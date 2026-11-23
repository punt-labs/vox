"""Start fields for a scripted or live Mode B call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import typer

from punt_vox.commands.voice_call_script import VoiceCallScript

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class VoiceCallOptions:
    """A recording to play or the caller's declaration of echo protection."""

    script: Path | None = None
    echo_control: str | None = None

    @classmethod
    def from_flags(cls, script: Path | None, *, headset: bool, aec: bool) -> Self:
        """Validate the CLI input choice before contacting the daemon."""
        if headset and aec:
            raise typer.BadParameter("--headset and --aec are mutually exclusive")
        echo_control = "headset" if headset else "aec" if aec else None
        if script is not None and echo_control is not None:
            raise typer.BadParameter("--headset/--aec apply only to live calls")
        return cls(script, echo_control)

    def wire(self) -> dict[str, object]:
        """Load the script if supplied and serialize the call start fields."""
        if self.script is not None:
            return {"script": VoiceCallScript.read(self.script).wire()}
        return {"echo_control": self.echo_control} if self.echo_control else {}
