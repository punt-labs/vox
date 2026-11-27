"""Exercise the exact 95% coverage floor gate and malformed inputs."""

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
        (95, 95, []),
        (100, 100, []),
        (94, 95, ["lines"]),
        (95, 94, ["branches"]),
        (94, 94, ["lines", "branches"]),
    ],
)
def test_below_floor(
    tmp_path: Path, lines: int, branches: int, expected: list[str]
) -> None:
    current = CoverageTotals(_report(tmp_path, "head.json", lines, branches))
    assert current.below_floor() == expected


def test_floor_is_exact_not_rounded(tmp_path: Path) -> None:
    path = tmp_path / "exact.json"
    path.write_text(
        json.dumps(
            {
                "totals": {
                    "covered_lines": 949999,
                    "num_statements": 1000000,
                    "covered_branches": 95,
                    "num_branches": 100,
                }
            }
        )
    )
    assert CoverageTotals(path).below_floor() == ["lines"]


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


def _run(monkeypatch: pytest.MonkeyPatch, head: Path, base: Path | None = None) -> int:
    argv = ["compare", str(head)]
    if base is not None:
        argv += ["--base", str(base)]
    monkeypatch.setattr("sys.argv", argv)
    return main()


def test_cli_fails_below_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    head = _report(tmp_path, "head.json", 949, 96)
    head.write_text(
        head.read_text().replace('"num_statements": 100', '"num_statements": 1000')
    )
    assert _run(monkeypatch, head) == 1
    assert "Coverage below the 95% floor: lines" in summary.read_text()


def test_cli_passes_at_floor_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert _run(monkeypatch, _report(tmp_path, "head.json", 95, 95)) == 0
    assert "Coverage floor 95%: met" in summary.read_text()


def test_cli_base_above_head_passes_and_is_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    base = _report(tmp_path, "base.json", 99, 99)
    head = _report(tmp_path, "head.json", 96, 96)
    assert _run(monkeypatch, head, base) == 0
    text = summary.read_text()
    assert "Merge base: lines 99.00%, branches 99.00%" in text
    assert "Current: lines 96.00%, branches 96.00%" in text
