"""The transient-error backoff sub-machine -- the Z ``retrying``/``failed`` transitions.

:class:`RetryMachine` wraps one :class:`ProgramState` and computes the successor
for each transient-error transition -- the retrying and failed resilience
states. It never mutates: every method re-validates the successor through
:meth:`ProgramState.with_updates` and returns it for the single writer to store.

The empty-pool guard on :meth:`retry_exhausted` and the non-empty self-loop of
:meth:`retry_capped` are what confine hard-failure to the empty-pool case:
a Program with a non-empty pool tolerates transient errors
indefinitely, recovering via :meth:`recover` while playback continues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn, Self, final

from punt_vox.types_programs.format import MAX_RETRY
from punt_vox.types_programs.mode import Mode
from punt_vox.voxd.programs.guard import GuardViolationError

if TYPE_CHECKING:
    from punt_vox.types_programs.identifiers import Reason
    from punt_vox.voxd.programs.state import ProgramState

__all__ = ["RetryMachine"]


@final
class RetryMachine:
    """The Z retrying/failed sub-machine over a single Program state."""

    __slots__ = ("_state",)
    _state: ProgramState

    def __new__(cls, state: ProgramState) -> Self:
        self = super().__new__(cls)
        self._state = state
        return self

    # -- entry into the backoff (from generation) --------------------------

    def first_track_transient(self, reason: Reason) -> ProgramState:
        """Back off after a transient first-Part error (Z ``FirstTrackTransient``)."""
        if self._state.mode is not Mode.GENERATING_FIRST:
            self._reject("first_track_transient requires mode generating_first")
        return self._state.with_updates(
            playing=None,
            last_played=None,
            mode=Mode.RETRYING,
            filling=False,
            attempts=1,
            last_error=reason,
        )

    def fill_transient(self, reason: Reason) -> ProgramState:
        """Back off on a transient fill error, playing on (Z ``FillTransient``)."""
        if not (self._state.mode is Mode.PLAYING_FILLING and self._state.filling):
            self._reject("fill_transient requires an active fill in playing_filling")
        return self._state.with_updates(
            mode=Mode.RETRYING,
            filling=False,
            attempts=1,
            last_error=reason,
        )

    # -- inside the backoff ------------------------------------------------

    def retry_fails(self, reason: Reason) -> ProgramState:
        """Count another transient error below the cap (Z ``RetryFails``)."""
        if self._state.mode is not Mode.RETRYING:
            self._reject("retry_fails requires mode retrying")
        if self._state.attempts >= MAX_RETRY:
            self._reject("retry_fails requires attempts below the cap")
        return self._state.with_updates(
            attempts=self._state.attempts + 1,
            last_error=reason,
        )

    def retry_exhausted(self, reason: Reason) -> ProgramState:
        """Give up an empty-pool Program at the cap (Z ``RetryExhausted``)."""
        if self._state.mode is not Mode.RETRYING:
            self._reject("retry_exhausted requires mode retrying")
        if self._state.attempts != MAX_RETRY:
            self._reject("retry_exhausted requires attempts at the cap")
        if len(self._state.pool) != 0:
            self._reject("retry_exhausted requires an empty pool")
        return self._state.with_updates(
            playing=None,
            last_played=None,
            mode=Mode.FAILED,
            filling=False,
            attempts=0,
            last_error=reason,
        )

    def retry_capped(self, reason: Reason) -> ProgramState:
        """Tolerate a transient at the cap on a non-empty pool (Z ``RetryCapped``).

        A non-empty pool never hard-fails: at the cap a further
        transient error neither exhausts (that needs an empty pool) nor climbs
        (``retry_fails`` guards below the cap), so the retry self-loops with
        ``attempts`` pinned and playback untouched -- it "plays on and keeps
        trying at the capped backoff". Only the advisory reason is refreshed.
        """
        if self._state.mode is not Mode.RETRYING:
            self._reject("retry_capped requires mode retrying")
        if self._state.attempts != MAX_RETRY:
            self._reject("retry_capped requires attempts at the cap")
        if len(self._state.pool) == 0:
            self._reject("retry_capped requires a non-empty pool")
        return self._state.with_updates(last_error=reason)

    # -- leaving the backoff -----------------------------------------------

    def recover(self) -> ProgramState:
        """Clear the backoff and resume generation (Z ``Recover``)."""
        if self._state.mode is not Mode.RETRYING:
            self._reject("recover requires mode retrying")
        act = self._state.activation(self._state.pool)
        keep_playing = None if len(self._state.pool) == 0 else self._state.playing
        return self._state.with_updates(
            mode=act.mode,
            filling=True,
            playing=keep_playing,
            attempts=0,
            last_error=None,
        )

    @staticmethod
    def _reject(message: str) -> NoReturn:
        """Raise a :class:`GuardViolationError` -- a violated Z precondition (guard)."""
        GuardViolationError.reject(message)
