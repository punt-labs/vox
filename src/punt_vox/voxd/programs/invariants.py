"""The Z ``Program`` schema predicate (invariants S1--S16) as a checker object.

Extracted from :class:`ProgramState` so the value object stays focused on state
and successor construction while this owns the predicate. It reads a state only
through its public observation API and raises ``ValueError`` on the first
violated invariant -- the mechanism that makes an illegal ``ProgramState``
unrepresentable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NoReturn, Self, final

from punt_vox.types_programs.format import MAX_RETRY
from punt_vox.types_programs.mode import Mode

if TYPE_CHECKING:
    from punt_vox.voxd.programs.state import ProgramState

__all__ = ["StateInvariants"]

_FILLING_MODES: Final = frozenset({Mode.GENERATING_FIRST, Mode.PLAYING_FILLING})
_ERROR_MODES: Final = frozenset({Mode.RETRYING, Mode.FAILED})


@final
class StateInvariants:
    """Validates the S1--S16 predicate of the Z ``Program`` schema against a state."""

    __slots__ = ("_s",)
    _s: ProgramState

    def __new__(cls, state: ProgramState) -> Self:
        self = super().__new__(cls)
        self._s = state
        return self

    def verify(self) -> None:
        """Raise ``ValueError`` on the first violated invariant.

        S2 (``#playing <= 1``, ``#lastPlayed <= 1``) and S3 (``#lastError <= 1``)
        hold structurally from the ``Part | None`` / ``Reason | None`` encoding,
        so they need no runtime guard; S1 and S4--S16 are checked here.
        """
        self._verify_cardinality()
        self._verify_mode_consistency()
        self._verify_mode_shape()

    def _verify_mode_shape(self) -> None:
        """Dispatch to the per-mode shape invariant S11--S16."""
        match self._s.mode:
            case Mode.OFF:
                self._verify_off()
            case Mode.GENERATING_FIRST:
                self._verify_generating_first()
            case Mode.PLAYING_FILLING:
                self._verify_playing_filling()
            case Mode.PLAYING_ROTATING:
                self._verify_playing_rotating()
            case Mode.RETRYING:
                self._verify_retrying()
            case Mode.FAILED:
                self._verify_failed()

    def _verify_cardinality(self) -> None:
        """Check the size and disjointness invariants S1, S4, S5, S6."""
        s = self._s
        cap = s.format.pool_size
        if len(s.pool) > cap:  # S1
            self._fail(f"S1: pool of {len(s.pool)} exceeds {s.format} capacity {cap}")
        if s.playing is not None and s.playing not in s.pool:  # S4
            self._fail("S4: the playing Part must be in the pool")
        if s.last_played is not None and s.last_played not in s.pool:  # S4
            self._fail("S4: the last-played Part must be in the pool")
        if s.failed_parts.parts & s.pool:  # S5
            self._fail("S5: a Part cannot be both ready and failed")
        if s.attempts > MAX_RETRY:  # S6
            self._fail(f"S6: attempts {s.attempts} exceeds cap {MAX_RETRY}")

    def _verify_mode_consistency(self) -> None:
        """Check the mode/flag consistency invariants S7, S8, S9, S10."""
        s = self._s
        if (s.attempts >= 1) != (s.mode is Mode.RETRYING):  # S7
            self._fail("S7: attempts >= 1 iff mode is retrying")
        if s.filling and s.mode not in _FILLING_MODES:  # S8
            self._fail("S8: filling implies generating_first or playing_filling")
        if s.last_error is not None and s.mode not in _ERROR_MODES:  # S9
            self._fail("S9: a program-level error implies retrying or failed")
        if s.mode is Mode.FAILED and s.last_error is None:  # S10
            self._fail("S10: failed implies an observable error")

    def _verify_off(self) -> None:  # S11
        s = self._s
        if not (
            s.playing is None
            and not s.filling
            and s.last_error is None
            and len(s.failed_parts) == 0
        ):
            self._fail("S11: off clears playing, filling, error, and failed parts")

    def _verify_generating_first(self) -> None:  # S12
        s = self._s
        if not (len(s.pool) == 0 and s.playing is None and s.filling):
            self._fail("S12: generating_first is empty, nothing playing, filling")

    def _verify_playing_filling(self) -> None:  # S13
        s = self._s
        if not (s.playing is not None and 1 <= len(s.pool) < s.format.pool_size):
            self._fail("S13: playing_filling plays one Part with a partial pool")

    def _verify_playing_rotating(self) -> None:  # S14
        s = self._s
        if not (
            s.playing is not None
            and len(s.pool) == s.format.pool_size
            and not s.filling
        ):
            self._fail(
                "S14: playing_rotating plays one Part with a full, unfilled pool"
            )

    def _verify_retrying(self) -> None:  # S15
        s = self._s
        if not (not s.filling and (len(s.pool) == 0) == (s.playing is None)):
            self._fail("S15: retrying pauses the fill; empty pool iff nothing playing")

    def _verify_failed(self) -> None:  # S16
        s = self._s
        if not (len(s.pool) == 0 and s.playing is None and not s.filling):
            self._fail("S16: failed has an empty pool, nothing playing, no fill")

    @staticmethod
    def _fail(message: str) -> NoReturn:
        raise ValueError(message)
