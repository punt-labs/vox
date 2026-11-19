"""Represent a combo's options and its chosen entry.

Lux requires a selected index even when repo state has no selection. A constant
leading (none) entry represents missing or unknown values without highlighting
a real choice. Keeping it present also keeps click indexes stable across a
refresh. The click resolver rejects that sentinel rather than unsetting state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final

__all__ = ["ChoiceList"]

_UNCHOSEN_ITEM: Final = "(none)"


@final
@dataclass(frozen=True, slots=True)
class ChoiceList:
    """One combo's items, its current value, and the wire view of both."""

    items: tuple[str, ...]
    current: str | None
    empty_label: str

    @property
    def is_empty(self) -> bool:
        """Return whether there is nothing to choose from at all.

        Distinct from having nothing chosen: an empty list means the combo
        should not publish, since no click on it could name anything.
        """
        return not self.items

    @property
    def _unchosen(self) -> bool:
        """Return whether the current value names no entry in the list."""
        return self.current is None or self.current not in self.items

    def wire_items(self) -> list[str]:
        """Return the item list the combo renders, sentinel included.

        The sentinel leads every non-empty list, whether or not anything is
        chosen. Showing it only while unchosen would make an index mean
        different things at different moments: a click carries an index
        picked from the list as it was *rendered*, and it is resolved
        against state read later. A refresh landing a provider in between
        would retire the sentinel and shift every index down by one, so a
        click on ``openai`` would commit ``polly`` -- a silently wrong
        write, not a refusal. A constant leading entry keeps the offset
        constant, which is the only version of this that cannot drift.
        """
        if self.is_empty:
            return [self.empty_label]
        return [_UNCHOSEN_ITEM, *self.items]

    def selected_index(self) -> int:
        """Return the selected index, or zero for the unchosen sentinel."""
        if self.is_empty or self._unchosen:
            return 0
        return self.items.index(self.current) + 1

    def name_for_index(self, index: int, *, noun: str) -> str:
        """Return the entry a clicked *index* names, or raise if it names none.

        Index ``0`` is the sentinel and names nothing: this panel offers no
        way to unset a provider, model or voice, so a click there is
        refused rather than quietly treated as the first real entry.

        *noun* is the caller's own singular word for one entry
        (``"voice"``), so a refusal reads in the caller's vocabulary rather
        than this type's, and agrees with its own count -- a list of one
        reads "1 voice", not "1 voices".

        Raises:
            ValueError: If position is outside the available items.
        """
        position = index - 1 if not self.is_empty else index
        if not 0 <= position < len(self.items):
            count = len(self.items)
            plural = noun if count == 1 else f"{noun}s"
            msg = f"{noun} combo: index {index} out of range for {count} {plural}"
            raise ValueError(msg)
        return self.items[position]
