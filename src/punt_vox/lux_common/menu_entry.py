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

    All three fields are required non-empty, and construction refuses an entry
    missing any of them: an empty callback id matches no click, an empty label
    shows a blank menu item, and an empty frame id reopens nothing -- each one a
    menu entry that silently does not work.
    """

    callback_id: str
    label: str
    frame_id: str

    def __post_init__(self) -> None:
        """Refuse an entry with an empty callback id, label, or frame id."""
        missing = [
            name
            for name, value in (
                ("callback_id", self.callback_id),
                ("label", self.label),
                ("frame_id", self.frame_id),
            )
            if not value
        ]
        if missing:
            msg = f"menu entry needs a non-empty {', '.join(missing)}: {self!r}"
            raise ValueError(msg)

    def owns(self, callback_id: str) -> bool:
        """Answer whether a click carrying *callback_id* came from this entry."""
        return callback_id == self.callback_id

    async def register_on(self, client: LuxClient) -> Ok | OpError:
        """Register this entry for *client*'s session."""
        return await client.callback.register(
            self.callback_id, self.label, self.frame_id
        )
