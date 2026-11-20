"""Exercise the exact merge-base coverage gate and malformed inputs."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from tools.compare_coverage import CoverageTotals, main


def _report(tmp_path: Path, name: str, lines: int = 80, branches: int = 50) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "totals": {
                    "covered_lines": lines,
                    "num_statements": 100,
                    "covered_branches": branches,
                    "num_branches": 100,
                }
            }
        )
    )
    return path


@pytest.mark.parametrize(
    ("lines", "branches", "expected"),
    [
        (80, 50, []),
        (81, 51, []),
        (79, 50, ["lines"]),
        (80, 49, ["branches"]),
        (79, 49, ["lines", "branches"]),
    ],
)
def test_regressions(
    tmp_path: Path, lines: int, branches: int, expected: list[str]
) -> None:
    base = CoverageTotals(_report(tmp_path, "base.xml"))
    current = CoverageTotals(_report(tmp_path, "head.xml", lines, branches))
    assert current.regressions(base) == expected


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "{}",
        '{"totals": {"covered_lines": 0, "num_statements": 0}}',
        '{"totals": {"covered_lines": 101, "num_statements": 100}}',
        '{"totals": {"covered_lines": -1, "num_statements": 100}}',
        '{"totals": {"covered_lines": 80.5, "num_statements": 100}}',
    ],
)
def test_invalid_report(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "invalid.xml"
    path.write_text(contents)
    with pytest.raises((ValueError, KeyError)):
        CoverageTotals(path)


@pytest.mark.parametrize(
    "counter", ["covered_lines", "num_statements", "covered_branches", "num_branches"]
)
@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, [], {}])
def test_rejects_non_integer_counters(
    tmp_path: Path, counter: str, value: object
) -> None:
    totals: dict[str, object] = {
        "covered_lines": 80,
        "num_statements": 100,
        "covered_branches": 50,
        "num_branches": 100,
    }
    totals[counter] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"totals": totals}))
    with pytest.raises(ValueError, match="Coverage counters must be integers"):
        CoverageTotals(path)


def test_missing_report(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        CoverageTotals(tmp_path / "missing.xml")


def test_ratios_are_read_only(tmp_path: Path) -> None:
    totals = CoverageTotals(_report(tmp_path, "report.json"))
    assert totals.lines == Fraction(4, 5)
    assert totals.branches == Fraction(1, 2)
    for name in ("lines", "branches"):
        with pytest.raises(AttributeError):
            setattr(totals, name, Fraction(1))


def test_cli_summary_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _report(tmp_path, "base.xml")
    head = _report(tmp_path, "head.xml", branches=49)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr("sys.argv", ["compare", str(head), "--base", str(base)])
    assert main() == 1
    assert "Coverage regression: branches" in summary.read_text()
    monkeypatch.setattr("sys.argv", ["compare", str(base)])
    assert main() == 0
    assert "Current: lines 80.00%, branches 50.00%" in summary.read_text()
