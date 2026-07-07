"""Tests for tools.oo_score -- the AST-based OO metric scorer.

Guards the PEP-570 fix: a positional-only receiver (`def m(self, x, /)`) must
count the same as the classic `def m(self, x)`, i.e. `self`/`cls` is subtracted
whether it lands in ``posonlyargs`` or ``args``.
"""

from __future__ import annotations

from tools.oo_score import ModuleMetrics


class TestAvgParamsReceiverSubtraction:
    """_avg_params subtracts the receiver from both arg positions."""

    def test_positional_only_self_matches_classic_self(self) -> None:
        posonly = ModuleMetrics(
            "posonly.py",
            "class C:\n    def apply(self, program, /) -> None: ...\n",
        )
        classic = ModuleMetrics(
            "classic.py",
            "class C:\n    def apply(self, program) -> None: ...\n",
        )
        assert posonly._avg_params() == classic._avg_params() == 1.0

    def test_positional_only_cls_is_subtracted(self) -> None:
        metrics = ModuleMetrics(
            "cls.py",
            "class C:\n    def make(cls, spec, /) -> None: ...\n",
        )
        assert metrics._avg_params() == 1.0

    def test_free_function_positional_only_arg_is_not_subtracted(self) -> None:
        metrics = ModuleMetrics("free.py", "def f(value, /) -> None: ...\n")
        assert metrics._avg_params() == 1.0

    def test_bare_receiver_only_counts_zero(self) -> None:
        metrics = ModuleMetrics(
            "bare.py",
            "class C:\n    def apply(self, /) -> None: ...\n",
        )
        assert metrics._avg_params() == 0.0
