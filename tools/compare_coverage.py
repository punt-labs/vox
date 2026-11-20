"""Publish total line/branch coverage and reject merge-base regressions."""

from __future__ import annotations

import argparse
import json
import os
from fractions import Fraction
from pathlib import Path
from typing import Self


class CoverageTotals:
    """Exact line and branch ratios from a nonempty coverage JSON report."""

    __slots__ = ("__lines", "__branches")

    __lines: Fraction
    __branches: Fraction

    def __new__(cls, path: Path) -> Self:
        """Read totals, rejecting reports with absent or invalid counters."""
        totals = json.loads(path.read_text())["totals"]
        self = super().__new__(cls)
        self.__lines = cls._ratio(totals["covered_lines"], totals["num_statements"])
        self.__branches = cls._ratio(totals["covered_branches"], totals["num_branches"])
        return self

    @property
    def lines(self) -> Fraction:
        """The read-only total line coverage ratio."""
        return self.__lines

    @property
    def branches(self) -> Fraction:
        """The read-only total branch coverage ratio."""
        return self.__branches

    @staticmethod
    def _ratio(covered: object, valid: object) -> Fraction:
        if type(covered) is not int or type(valid) is not int:
            raise ValueError("Coverage counters must be integers")
        if valid <= 0 or not 0 <= covered <= valid:
            raise ValueError(f"Invalid coverage totals: {covered}/{valid}")
        return Fraction(covered, valid)

    def summary(self, label: str) -> str:
        """Render percentages for humans without rounding the comparison."""
        return (
            f"{label}: lines {float(self.lines):.2%}, "
            f"branches {float(self.branches):.2%}"
        )

    def regressions(self, base: CoverageTotals) -> list[str]:
        """Name each metric that decreased, using exact rational comparisons."""
        return [
            name
            for name in ("lines", "branches")
            if getattr(self, name) < getattr(base, name)
        ]


def main() -> int:
    """Publish current/base totals and fail closed on either regression."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current", type=Path)
    parser.add_argument("--base", type=Path)
    args = parser.parse_args()
    current = CoverageTotals(args.current)
    messages = [current.summary("Current")]
    regressions = []
    if args.base is not None:
        base = CoverageTotals(args.base)
        messages.append(base.summary("Merge base"))
        regressions = current.regressions(base)
        messages.append(
            f"Coverage regression: {', '.join(regressions)}"
            if regressions
            else "No coverage regression."
        )
    report = "\n".join(messages)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write(f"## Combined test coverage\n\n{report}\n")
    return int(bool(regressions))


if __name__ == "__main__":
    raise SystemExit(main())
