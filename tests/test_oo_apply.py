"""Unit tests for the pure metric helpers on PlanApplier."""

from __future__ import annotations

from tools.oo_ratchet.apply import PlanApplier


class TestRegressed:
    """`regressed` reports metrics worse than the base, direction-aware."""

    def test_new_file_has_no_regressions(self) -> None:
        assert PlanApplier.regressed({"max_complexity": 40.0}, None) == []

    def test_worse_metric_is_regressed(self) -> None:
        base = {"max_complexity": 1.0}
        worse = {"max_complexity": 3.0}
        assert PlanApplier.regressed(worse, base) == ["max_complexity"]

    def test_better_or_equal_metric_is_not_regressed(self) -> None:
        base = {"max_complexity": 3.0}
        assert PlanApplier.regressed({"max_complexity": 1.0}, base) == []


class TestDeltas:
    """`deltas` records the changed metrics as old->new pairs."""

    def test_new_file_records_all_metrics_from_zero(self) -> None:
        assert PlanApplier.deltas({"module_size": 5.0}, None) == {
            "module_size": [0.0, 5.0]
        }

    def test_only_changed_metrics_recorded(self) -> None:
        base = {"module_size": 5.0, "max_complexity": 2.0}
        entry = {"module_size": 7.0, "max_complexity": 2.0}
        assert PlanApplier.deltas(entry, base) == {"module_size": [5.0, 7.0]}
