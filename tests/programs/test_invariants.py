"""Each S-invariant is validated in the constructor -- illegal states raise.

One test per invariant S1--S16 (S2/S3 hold structurally via the ``Part | None``
encoding). Each builds a state that violates exactly one invariant and asserts
the corresponding ``ValueError`` -- proving illegal states are unrepresentable.
"""

from __future__ import annotations

from typing import Any

import pytest

from punt_vox.types_programs import Format, Mode, Reason
from punt_vox.voxd.programs import FrozenParts, Part, ProgramState


def _p(index: int) -> Part:
    return Part(f"id{index:03d}", index)


def _pool(*indices: int) -> frozenset[Part]:
    return frozenset(_p(i) for i in indices)


def _state(**overrides: Any) -> ProgramState:
    """Build a ProgramState from the legal off baseline plus field overrides."""
    fields: dict[str, Any] = {
        "fmt": Format.PLAYLIST,
        "pool": frozenset(),
        "failed_parts": FrozenParts.empty(),
        "playing": None,
        "last_played": None,
        "mode": Mode.OFF,
        "filling": False,
        "attempts": 0,
        "last_error": None,
    }
    fields.update(overrides)
    return ProgramState(**fields)


def test_baseline_is_legal() -> None:
    assert _state().mode is Mode.OFF


def test_s1_bounded_pool() -> None:
    with pytest.raises(ValueError, match="S1"):
        _state(pool=_pool(*range(1, 14)))


def test_s4_playing_in_pool() -> None:
    with pytest.raises(ValueError, match="S4"):
        _state(
            mode=Mode.PLAYING_FILLING, pool=_pool(1, 2), playing=_p(99), filling=True
        )


def test_s4_last_played_in_pool() -> None:
    with pytest.raises(ValueError, match="S4"):
        _state(
            mode=Mode.PLAYING_FILLING,
            pool=_pool(1, 2),
            playing=_p(1),
            last_played=_p(99),
            filling=True,
        )


def test_s5_ready_and_failed_disjoint() -> None:
    with pytest.raises(ValueError, match="S5"):
        _state(
            mode=Mode.PLAYING_FILLING,
            pool=_pool(1, 2),
            playing=_p(1),
            filling=True,
            failed_parts=FrozenParts.empty().with_failure(_p(1), Reason("dup")),
        )


def test_s6_attempts_capped() -> None:
    with pytest.raises(ValueError, match="S6"):
        _state(mode=Mode.RETRYING, attempts=6, last_error=Reason("x"))


def test_s7_attempts_iff_retrying_forward() -> None:
    with pytest.raises(ValueError, match="S7"):
        _state(mode=Mode.PLAYING_FILLING, pool=_pool(1, 2), playing=_p(1), attempts=1)


def test_s7_attempts_iff_retrying_reverse() -> None:
    with pytest.raises(ValueError, match="S7"):
        _state(mode=Mode.RETRYING, attempts=0)


def test_s8_filling_implies_generating_or_filling() -> None:
    with pytest.raises(ValueError, match="S8"):
        _state(
            mode=Mode.PLAYING_ROTATING,
            pool=_pool(*range(1, 13)),
            playing=_p(1),
            filling=True,
        )


def test_s9_error_implies_retrying_or_failed() -> None:
    with pytest.raises(ValueError, match="S9"):
        _state(
            mode=Mode.PLAYING_FILLING,
            pool=_pool(1, 2),
            playing=_p(1),
            filling=True,
            last_error=Reason("x"),
        )


def test_s10_failed_implies_observable_error() -> None:
    with pytest.raises(ValueError, match="S10"):
        _state(mode=Mode.FAILED, last_error=None)


def test_s11_off_clears_residue() -> None:
    with pytest.raises(ValueError, match="S11"):
        _state(failed_parts=FrozenParts.empty().with_failure(_p(1), Reason("x")))


def test_s12_generating_first_is_empty() -> None:
    with pytest.raises(ValueError, match="S12"):
        _state(mode=Mode.GENERATING_FIRST, pool=_pool(1), filling=True)


def test_s13_playing_filling_needs_a_playing_part() -> None:
    with pytest.raises(ValueError, match="S13"):
        _state(mode=Mode.PLAYING_FILLING, pool=_pool(1), playing=None, filling=True)


def test_s14_playing_rotating_needs_a_full_pool() -> None:
    with pytest.raises(ValueError, match="S14"):
        _state(mode=Mode.PLAYING_ROTATING, pool=_pool(1), playing=_p(1))


def test_s15_retrying_empty_pool_iff_nothing_playing() -> None:
    with pytest.raises(ValueError, match="S15"):
        _state(
            mode=Mode.RETRYING,
            pool=_pool(1),
            playing=None,
            attempts=1,
            last_error=Reason("x"),
        )


def test_s16_failed_is_empty() -> None:
    with pytest.raises(ValueError, match="S16"):
        _state(mode=Mode.FAILED, pool=_pool(1), last_error=Reason("x"))
