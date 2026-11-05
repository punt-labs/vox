"""``Deadline``: one bound a whole operation shares, cut into each step's timeout."""

from __future__ import annotations

from typing import Self, final

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.deadline import Deadline


@final
class _Now:
    """A settable monotonic reading."""

    __slots__ = ("t",)

    t: float

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.t = 0.0
        return self

    def __call__(self) -> float:
        return self.t


def test_remaining_counts_down_and_stops_at_zero() -> None:
    now = _Now()
    deadline = Deadline(now, 5.0)
    now.t = 2.0
    assert deadline.remaining() == 3.0
    now.t = 9.0
    assert deadline.remaining() == 0.0


def test_cap_cuts_a_step_timeout_to_what_remains() -> None:
    now = _Now()
    deadline = Deadline(now, 5.0)
    assert deadline.cap(0.5, "connect") == 0.5
    now.t = 4.8
    assert deadline.cap(5.0, "fuser") == pytest.approx(0.2)


def test_cap_after_the_deadline_raises_naming_the_step_and_bound() -> None:
    now = _Now()
    deadline = Deadline(now, 5.0)
    now.t = 5.0
    with pytest.raises(PortProbeError, match=r"cannot run fuser: the 5s deadline"):
        deadline.cap(5.0, "cannot run fuser")


def test_never_leaves_every_step_its_own_timeout() -> None:
    assert Deadline.never().cap(5.0, "ps") == 5.0
