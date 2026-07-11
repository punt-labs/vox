"""Tests for the AST-based OO metric scorer.

Guards the PEP-570 fix: a positional-only receiver (`def m(self, x, /)`) must
count the same as the classic `def m(self, x)`, i.e. `self`/`cls` is subtracted
whether it lands in ``posonlyargs`` or ``args``.
"""

from __future__ import annotations

from tools.oo_ratchet.metrics import ModuleMetrics


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


_PROTOCOL_TYPEDDICT = """from __future__ import annotations

from typing import Protocol, TypedDict


class Port(Protocol):
    def go(self) -> None: ...


class Row(TypedDict):
    x: int


class Real:
    _a: int

    def __new__(cls) -> "Real":
        self = super().__new__(cls)
        self._a = 1
        return self
"""

_DATACLASS_AND_INIT = """from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Value:
    x: int


class Classic:
    def __init__(self) -> None:
        self._y = 1
"""

_ENCAPSULATION = """class C:
    def __new__(cls):
        self = super().__new__(cls)
        self._priv = 1
        self.pub = 2
        return self
"""

_BRANCHY = """def f(x):
    if x > 0:
        if x > 10:
            return 1
        return 2
    return 3
"""


class TestClassCounting:
    """_count_classes excludes Protocol and TypedDict definitions."""

    def test_protocol_and_typeddict_excluded(self) -> None:
        metrics = ModuleMetrics("m.py", _PROTOCOL_TYPEDDICT)
        assert metrics._count_classes() == 1  # only Real

    def test_class_to_func_ratio_half(self) -> None:
        src = "class A:\n    _x: int\n\n\ndef g() -> None: ...\n"
        assert ModuleMetrics("m.py", src)._class_to_func_ratio() == 0.5


class TestInitAndDataclass:
    """__init__ counts only in non-dataclass classes."""

    def test_dataclass_init_not_counted(self) -> None:
        metrics = ModuleMetrics("m.py", _DATACLASS_AND_INIT)
        assert metrics._count_init() == 1  # Classic only; Value is a dataclass


class TestEncapsulation:
    """encapsulation_ratio and public-attr counting."""

    def test_ratio_and_public_count(self) -> None:
        metrics = ModuleMetrics("m.py", _ENCAPSULATION)
        assert metrics._encapsulation_ratio() == 0.5
        assert metrics._count_public_attrs() == 1


class TestComplexity:
    """Cyclomatic complexity over nested branches."""

    def test_max_and_avg_complexity(self) -> None:
        metrics = ModuleMetrics("m.py", _BRANCHY)
        assert metrics._max_complexity() == 3  # 1 + two nested ifs
        assert metrics._avg_complexity() == 3.0  # single function


class TestModuleShape:
    """future-annotations detection and module size."""

    def test_future_annotations_present(self) -> None:
        src = "from __future__ import annotations\n\n\nclass A:\n    _x: int\n"
        assert ModuleMetrics("m.py", src)._has_future_annotations() == 1

    def test_future_annotations_absent(self) -> None:
        metrics = ModuleMetrics("m.py", "class A:\n    _x: int\n")
        assert metrics._has_future_annotations() == 0

    def test_module_size_counts_nonblank_lines(self) -> None:
        src = "from __future__ import annotations\n\n\nclass A:\n    _x: int\n"
        assert ModuleMetrics("m.py", src).compute()["module_size"] == 3
