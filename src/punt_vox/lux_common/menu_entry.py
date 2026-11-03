"""``MenuEntry`` -- a Lux menu item a vox surface registers, and its frame."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from punt_lux import LuxClient, OpError
    from punt_lux.operations import Ok

__all__ = ["MenuEntry"]


@final
@dataclass(frozen=True, slots=True)
class MenuEntry:
    """The id a click carries back, the label shown, and the frame the click reopens.

    ``frame_id`` is the raw id the surface's scene is pushed into. Naming it at
    registration is what lets the display reopen a window the user closed: a
    content push never does, so an entry registered without it reopens nothing.
    """

    callback_id: str
    label: str
    frame_id: str

    async def register_on(self, client: LuxClient) -> Ok | OpError:
        """Register this entry for *client*'s session."""
        return await client.callback.register(
            self.callback_id, self.label, self.frame_id
        )
