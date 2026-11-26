"""Hold consequential requests until the person confirms each one aloud."""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.voice_call.spoken_answer import SpokenAnswer

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.provenance import CallProvenance

__all__ = ["ConfirmationGate"]

_RECENT: Final = 8
_ECHO_MIN_WORDS: Final = 4  # A short "yes" is the person; a sentence is an echo.
_INSTRUCTION: Final = (
    "Read voxd_readback to the person aloud, word for word, then wait for "
    "their answer. Do nothing else and do not call this tool again until "
    "they have answered."
)


@final
class _Pending:
    """One held request: its read-back, whether it was spoken, and its clearance."""

    __slots__ = ("_cleared_at", "_key", "_readback", "_spoken")
    _key: str
    _readback: str
    _spoken: bool
    _cleared_at: int | None  # The taint epoch the person's yes was given in.

    def __new__(cls, key: str, readback: str) -> Self:
        """Hold a request whose read-back has not been spoken yet."""
        self = super().__new__(cls)
        self._key, self._readback = key, readback
        self._spoken, self._cleared_at = False, None
        return self

    @property
    def key(self) -> str:
        """Return the canonical key of the held request."""
        return self._key

    @property
    def readback(self) -> str:
        """Return the daemon's text for the held request."""
        return self._readback

    @property
    def spoken(self) -> bool:
        """Return whether the agent has said the read-back."""
        return self._spoken

    @property
    def has_clearance(self) -> bool:
        """Return whether a yes was ever recorded, current or revoked."""
        return self._cleared_at is not None

    def mark_spoken(self) -> None:
        """Record that the agent said the read-back."""
        self._spoken = True

    def clear(self, epoch: int) -> None:
        """Record the person's yes, given while the taint was at ``epoch``."""
        self._cleared_at = epoch

    def is_cleared(self, epoch: int) -> bool:
        """Return whether a yes was given and no untrusted text arrived since."""
        return self._cleared_at == epoch


@final
class ConfirmationGate:
    """Allow a held request to run only after a spoken yes to its own read-back.

    A call-wide flag would let one ordinary reply release whatever injected
    text asked for. Each request is held by its canonical key; the person's yes
    releases only the newest request whose read-back the agent said, once, and
    is void if untrusted text arrived after it. The gate runs before the result
    cache, so a refusal is never stored and cannot be replayed.
    """

    __slots__ = ("_pending", "_provenance", "_recent", "_released")
    _provenance: CallProvenance
    _pending: dict[str, _Pending]
    _released: set[str]  # Keys that ran after a yes; a replay needs no second one.
    _recent: deque[str]  # What the agent and the read-backs recently said.

    def __new__(cls, provenance: CallProvenance) -> Self:
        """Bind the call's provenance."""
        self = super().__new__(cls)
        self._provenance = provenance
        self._pending, self._released = {}, set()
        self._recent = deque(maxlen=_RECENT)
        return self

    def check(self, key: str, readback: str) -> str | None:
        """Return ``None`` to run the request, else the result that holds it."""
        if not self._provenance.tainted or key in self._released:
            return None
        epoch = self._provenance.epoch
        pending = self._pending.get(key)
        if pending is not None and pending.is_cleared(epoch):
            del self._pending[key]
            self._released.add(key)
            return None
        if self._needs_new_readback(pending, readback):
            self._hold(key, readback)
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "voxd_readback": readback,
                "instruction": _INSTRUCTION,
            },
            ensure_ascii=False,
        )

    def agent_spoke(self, text: str) -> None:
        """Note the agent's reply; one that contains a read-back counts as saying it."""
        words = SpokenAnswer.normalize(text)
        self._recent.append(words)
        for pending in self._pending.values():
            if SpokenAnswer.normalize(pending.readback) in words:
                pending.mark_spoken()

    def heard(self, text: str) -> None:
        """Apply what the person said to the newest request whose read-back was said."""
        words = SpokenAnswer.normalize(text)
        if not words or self._is_echo(words):
            return
        answer = SpokenAnswer.of(text)
        if answer == "other":
            return
        target = next((p for p in reversed(self._pending.values()) if p.spoken), None)
        if target is None:
            return
        if answer == "yes":
            target.clear(self._provenance.epoch)
        else:
            del self._pending[target.key]

    @staticmethod
    def _needs_new_readback(pending: _Pending | None, readback: str) -> bool:
        """Return whether the request must be read back again.

        A revoked clearance counts: the person's old yes was to a conversation
        that has since taken in more untrusted text.
        """
        return (
            pending is None
            or pending.readback != readback
            or pending.has_clearance
            or not pending.spoken
        )

    def _hold(self, key: str, readback: str) -> None:
        self._pending.pop(key, None)
        self._pending[key] = _Pending(key, readback)
        while len(self._pending) > _RECENT:
            del self._pending[next(iter(self._pending))]

    def _is_echo(self, words: str) -> bool:
        """Return whether a sentence-length transcript is the agent's own speech."""
        if len(words.split()) < _ECHO_MIN_WORDS:
            return False
        return any(f" {words} " in f" {said} " for said in self._recent)
