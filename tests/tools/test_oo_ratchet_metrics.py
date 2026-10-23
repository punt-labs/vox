"""Cyclomatic complexity as the OO ratchet's ModuleMetrics measures it."""

from __future__ import annotations

import textwrap

import pytest

from tools.oo_ratchet.metrics import ModuleMetrics

_STRAIGHT_LINE = """
def f(x: int) -> int:
    return x
"""

_IF_LADDER = """
def f(x: object) -> int:
    if isinstance(x, int):
        return 1
    elif isinstance(x, str):
        return 2
    return 0
"""

_MATCH_EQUIVALENT = """
def f(x: object) -> int:
    match x:
        case int():
            return 1
        case str():
            return 2
    return 0
"""

_GUARDED_IF = """
def f(x: object, flag: bool) -> int:
    if isinstance(x, int) and flag:
        return 1
    return 0
"""

_GUARDED_MATCH = """
def f(x: object, flag: bool) -> int:
    match x:
        case int() if flag:
            return 1
    return 0
"""

_OR_IF = """
def f(x: object) -> int:
    if isinstance(x, int) or isinstance(x, str):
        return 1
    return 0
"""

_OR_MATCH = """
def f(x: object) -> int:
    match x:
        case int() | str():
            return 1
    return 0
"""


def _complexity(source: str) -> int:
    """Return the max_complexity the ratchet records for a one-function module."""
    score = ModuleMetrics("m.py", textwrap.dedent(source)).compute()["max_complexity"]
    assert isinstance(score, int)
    return score


def _match_with_cases(count: int) -> str:
    """Return a function whose only branching is a match of *count* plain cases."""
    arms = "".join(
        f"        case {value}:\n            return {value}\n" for value in range(count)
    )
    return f"def f(x: int) -> int:\n    match x:\n{arms}    return -1\n"


@pytest.mark.parametrize("cases", [1, 2, 3, 7])
def test_each_match_case_adds_one(cases: int) -> None:
    """A match of N cases scores N more than a straight-line function."""
    assert _complexity(_match_with_cases(cases)) == _complexity(_STRAIGHT_LINE) + cases


def test_wildcard_case_counts_as_a_case() -> None:
    """``case _`` is an ast.match_case like any other and adds one."""
    source = """
    def f(x: int) -> int:
        match x:
            case 0:
                return 0
            case _:
                return 1
    """
    assert _complexity(source) == 3


def test_case_guard_adds_one() -> None:
    """A guarded case scores one more than the same case unguarded."""
    unguarded = _GUARDED_MATCH.replace(" if flag", "")
    assert _complexity(_GUARDED_MATCH) == _complexity(unguarded) + 1


@pytest.mark.parametrize("alternatives", [2, 3, 5])
def test_or_pattern_adds_one_per_extra_alternative(alternatives: int) -> None:
    """``case 0 | 1 | 2`` adds what ``x == 0 or x == 1 or x == 2`` adds."""
    pattern = " | ".join(str(value) for value in range(alternatives))
    source = f"""
    def f(x: int) -> int:
        match x:
            case {pattern}:
                return 1
        return 0
    """
    assert _complexity(source) == _complexity(_STRAIGHT_LINE) + alternatives


def test_conditional_expression_adds_one() -> None:
    """``a if cond else b`` is a decision and scores like an if statement."""
    source = """
    def f(x: int) -> int:
        return 1 if x else 0
    """
    assert _complexity(source) == _complexity(_STRAIGHT_LINE) + 1


def test_nested_conditional_expressions_each_add_one() -> None:
    """Every IfExp node counts, including one nested in another's orelse."""
    source = """
    def f(x: int) -> int:
        return 1 if x > 0 else -1 if x < 0 else 0
    """
    assert _complexity(source) == 3


@pytest.mark.parametrize(
    ("match_source", "ladder_source"),
    [
        pytest.param(_MATCH_EQUIVALENT, _IF_LADDER, id="two-arm"),
        pytest.param(_GUARDED_MATCH, _GUARDED_IF, id="guarded"),
        pytest.param(_OR_MATCH, _OR_IF, id="or-pattern"),
    ],
)
def test_match_scores_like_its_if_ladder(match_source: str, ladder_source: str) -> None:
    """A match and the if-ladder that tests the same conditions score the same."""
    assert _complexity(match_source) == _complexity(ladder_source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(_STRAIGHT_LINE, 1, id="straight-line"),
        pytest.param(_IF_LADDER, 3, id="if-elif"),
        pytest.param(_GUARDED_IF, 3, id="if-and"),
        pytest.param(
            """
            def f(xs: list[int]) -> list[int]:
                return [x for x in xs if x]
            """,
            3,
            id="comprehension-with-if",
        ),
        pytest.param(
            """
            def f(x: int) -> int:
                try:
                    assert x
                    while x:
                        x -= 1
                except ValueError:
                    return 0
                return x
            """,
            4,
            id="assert-while-except",
        ),
        pytest.param(
            """
            def f(xs: list[int]) -> int:
                total = 0
                for x in xs:
                    total += x
                return total
            """,
            2,
            id="for",
        ),
        pytest.param(
            """
            from collections.abc import AsyncIterator

            async def f(xs: AsyncIterator[int]) -> int:
                total = 0
                async for x in xs:
                    total += x
                return total
            """,
            2,
            id="async-for",
        ),
    ],
)
def test_existing_constructs_score_unchanged(source: str, expected: int) -> None:
    """Branches the ratchet already counted keep their scores."""
    assert _complexity(source) == expected
