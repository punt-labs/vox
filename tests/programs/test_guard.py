"""Tests for GuardViolationError -- the guard-failure exception and its raiser."""

from __future__ import annotations

import pytest

from punt_vox.voxd.programs.guard import GuardViolationError


class TestGuardViolationError:
    def test_is_a_value_error(self) -> None:
        # Callers that only distinguish "illegal transition" catch ValueError.
        assert issubclass(GuardViolationError, ValueError)

    def test_reject_raises_with_the_message(self) -> None:
        with pytest.raises(GuardViolationError, match="rotate after off") as exc:
            GuardViolationError.reject("rotate after off")
        assert str(exc.value) == "rotate after off"

    def test_reject_is_the_narrow_race_type(self) -> None:
        # The single writer catches *this* type alone, never a bare ValueError.
        with pytest.raises(GuardViolationError):
            GuardViolationError.reject("lost race")
