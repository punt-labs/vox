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

    def test_non_utf8_file_raises_baseline_error(self, tmp_path: Path) -> None:
        # A non-UTF8 baseline file raises UnicodeDecodeError on read_text; _load
        # must turn it into the typed error, not a traceback.
        (tmp_path / Baseline.FILENAME).write_bytes(b"\xff\xfe\x00")
        with pytest.raises(BaselineError):
            Baseline(tmp_path)

    def test_non_dict_file_raises_baseline_error(self, tmp_path: Path) -> None:
        # Valid JSON but not an object (a list) -> controlled typed error, not an
        # AttributeError on .get() downstream.
        (tmp_path / Baseline.FILENAME).write_text("[1, 2, 3]")
        with pytest.raises(BaselineError):
            Baseline(tmp_path)

    def test_nested_non_dict_entry_raises_baseline_error(self, tmp_path: Path) -> None:
        # {"a.py": "garbage"} passes the top-level dict check but the value is not
        # a metric dict; without the nested guard `metric not in "garbage"` is a
        # substring test that skips every metric -> fail-OPEN. Reject it.
        (tmp_path / Baseline.FILENAME).write_text('{"a.py": "garbage"}')
        with pytest.raises(BaselineError):
            Baseline(tmp_path)

    def test_bool_metric_raises_baseline_error(self, tmp_path: Path) -> None:
        # A bool metric value (`true`) is an int subclass that would compare as
        # 0/1 -- fail-open. Reject it at load with the typed error.
        (tmp_path / Baseline.FILENAME).write_text('{"a.py": {"module_size": true}}')
        with pytest.raises(BaselineError):
            Baseline(tmp_path)

    def test_string_metric_raises_baseline_error(self, tmp_path: Path) -> None:
        # A string metric value would raise TypeError in the numeric comparison;
        # reject it at load with the typed error instead.
        (tmp_path / Baseline.FILENAME).write_text('{"a.py": {"module_size": "5"}}')
        with pytest.raises(BaselineError):
            Baseline(tmp_path)
