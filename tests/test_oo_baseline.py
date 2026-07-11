"""Unit tests for the in-tree baseline load/query."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.oo_ratchet.baseline import Baseline, BaselineError


class TestBaselineLoad:
    """Loading is fail-closed but readable on a corrupt file."""

    def test_absent_file_is_empty(self, tmp_path: Path) -> None:
        assert Baseline(tmp_path).entries == {}
        assert Baseline(tmp_path).exists is False

    def test_valid_file_loads(self, tmp_path: Path) -> None:
        (tmp_path / Baseline.FILENAME).write_text('{"a.py": {"module_size": 3.0}}')
        assert Baseline(tmp_path).get("a.py") == {"module_size": 3.0}

    def test_corrupt_file_raises_baseline_error(self, tmp_path: Path) -> None:
        (tmp_path / Baseline.FILENAME).write_text("{ not valid json")
        with pytest.raises(BaselineError):
            Baseline(tmp_path)
