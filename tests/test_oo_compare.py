"""Unit tests for threshold semantics and per-file/whole-change review logic."""

from __future__ import annotations

from tools.oo_ratchet.compare import FileReview, Review
from tools.oo_ratchet.thresholds import Thresholds

_NONE: frozenset[tuple[str, str]] = frozenset()
_WAIVE_W = frozenset({("sub/w.py", "max_complexity")})
_WAIVE_A = frozenset({("sub/a.py", "max_complexity")})


class TestThresholds:
    """Direction-aware comparison for <=, >=, and == metrics."""

    def test_le_metric_lower_is_better(self) -> None:
        assert Thresholds.strictly_better("max_complexity", 1.0, 3.0)
        assert not Thresholds.strictly_better("max_complexity", 3.0, 1.0)

    def test_ge_metric_higher_is_better(self) -> None:
        assert Thresholds.strictly_better("method_ratio", 0.9, 0.8)
        assert Thresholds.better_or_equal("method_ratio", 0.8, 0.8)

    def test_eq_metric_closer_to_target_is_better(self) -> None:
        # target is 1 for future_annotations; 1 is better than 0.
        assert Thresholds.strictly_better("future_annotations", 1.0, 0.0)
        assert not Thresholds.strictly_better("future_annotations", 0.0, 1.0)

    def test_meets_absolute_threshold(self) -> None:
        assert Thresholds.meets("module_size", 300.0)
        assert not Thresholds.meets("module_size", 301.0)


class TestFileReviewTracked:
    """A file present in the base baseline compares metric-by-metric."""

    def test_improvement_is_detected(self) -> None:
        review = FileReview(
            "sub/w.py",
            {"max_complexity": 1.0},
            {"max_complexity": 3.0},
            {"max_complexity": 1.0},
            _NONE,
        )
        assert review.improved
        assert not review.regressed

    def test_regression_is_detected(self) -> None:
        review = FileReview(
            "sub/w.py",
            {"max_complexity": 3.0},
            {"max_complexity": 1.0},
            {"max_complexity": 3.0},
            _NONE,
        )
        assert review.regressed == ("max_complexity",)
        assert not review.improved

    def test_regression_waived_when_locked_and_relaxed(self) -> None:
        review = FileReview(
            "sub/w.py",
            {"max_complexity": 3.0},
            {"max_complexity": 1.0},
            {"max_complexity": 3.0},  # in-tree baseline locks the current value
            _WAIVE_W,  # relaxation belongs to the current change
        )
        assert review.waived == ("max_complexity",)
        assert not review.regressed

    def test_no_waiver_without_lock(self) -> None:
        review = FileReview(
            "sub/w.py",
            {"max_complexity": 4.0},
            {"max_complexity": 1.0},
            {"max_complexity": 3.0},  # in-tree baseline != current -> not locked
            _WAIVE_W,
        )
        assert review.regressed == ("max_complexity",)
        assert not review.waived

    def test_no_waiver_when_relaxation_not_in_current_change(self) -> None:
        # Locked to a worse value, but no relaxation scoped to this change.
        review = FileReview(
            "sub/w.py",
            {"max_complexity": 3.0},
            {"max_complexity": 1.0},
            {"max_complexity": 3.0},
            _NONE,
        )
        assert review.regressed == ("max_complexity",)
        assert not review.waived


class TestFileReviewNew:
    """A file absent from the base baseline is judged on absolute thresholds."""

    def test_new_file_passing_thresholds(self) -> None:
        review = FileReview("sub/n.py", {"max_complexity": 2.0}, None, None, _NONE)
        assert review.new_passes
        assert not review.regressed

    def test_new_file_failing_thresholds(self) -> None:
        review = FileReview("sub/n.py", {"max_complexity": 40.0}, None, None, _NONE)
        assert not review.new_passes
        assert review.regressed == ("max_complexity",)


def _tracked(path: str, *, improved: bool) -> FileReview:
    base = {"max_complexity": 3.0 if improved else 1.0}
    current = {"max_complexity": 1.0}
    return FileReview(path, current, base, current, _NONE)


class TestReviewGate:
    """The S5 improvement gate and its pure-add and waiver exemptions."""

    def test_existing_file_must_improve(self) -> None:
        stagnant = _tracked("sub/a.py", improved=False)
        new_ok = FileReview("sub/b.py", {"max_complexity": 2.0}, None, None, _NONE)
        review = Review((stagnant, new_ok))
        # An existing file is touched; a passing new file does not satisfy it.
        assert not review.improvement_satisfied

    def test_existing_file_improvement_satisfies(self) -> None:
        improved = _tracked("sub/a.py", improved=True)
        assert Review((improved,)).improvement_satisfied

    def test_pure_add_satisfied_by_new_file(self) -> None:
        new_ok = FileReview("sub/b.py", {"max_complexity": 2.0}, None, None, _NONE)
        assert Review((new_ok,)).improvement_satisfied

    def test_waiver_exempts_gate(self) -> None:
        waived = FileReview(
            "sub/a.py",
            {"max_complexity": 3.0},
            {"max_complexity": 1.0},
            {"max_complexity": 3.0},
            _WAIVE_A,
        )
        review = Review((waived,))
        assert review.has_waiver
        assert review.improvement_satisfied
        assert not review.has_regression
