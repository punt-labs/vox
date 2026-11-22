"""Bounded input receipts ordered by sender sequence, not polling or arrival."""

from __future__ import annotations

from typing import Self, final

from punt_vox.voxd.hook_context_projection import HookText


@final
class HookInputHistory:
    """Retain the newest submitted inputs independently of turn completion."""

    __slots__ = ("_inputs",)
    _inputs: dict[int, str]
    LIMIT = 32

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._inputs = {}
        return self

    def ingest(self, sequence: int, prompt: object) -> None:
        """Keep the first observation of each retained sequence."""
        self._inputs.setdefault(sequence, HookText.clip(prompt))
        if len(self._inputs) > self.LIMIT:
            del self._inputs[min(self._inputs)]

    def last(self) -> tuple[int, str]:
        """Return a zero cursor until any input has been observed."""
        sequence = max(self._inputs, default=0)
        return sequence, self._inputs.get(sequence, "")

    def since(self, baseline: int) -> list[tuple[int, str]]:
        """Return retained receipts strictly newer than the caller's cursor."""
        return [
            (seq, text) for seq, text in sorted(self._inputs.items()) if seq > baseline
        ]
