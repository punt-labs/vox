"""Command-line entry point: parse arguments and dispatch to the ratchet."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .baseline import SuppressionBaseline, SuppressionBaselineError
from .gitio import GitError
from .outcome import Outcome
from .report import SuppressionReport
from .scanner import Scanner


@dataclass(frozen=True, slots=True)
class Options:
    """Parsed command-line options for one suppression ratchet invocation."""

    src: Path
    check: bool
    update: bool
    json: bool
    threshold: bool
    base_ref: str | None
    require_base: bool

    @classmethod
    def parse(cls, argv: list[str] | None = None) -> Self:
        """Build options from ``argv`` (defaults to ``sys.argv``)."""
        ns = cls._build_parser().parse_args(argv)
        return cls(
            src=Path(ns.src),
            check=bool(ns.check),
            update=bool(ns.update),
            json=bool(ns.json),
            threshold=bool(ns.threshold),
            base_ref=ns.base_ref,
            require_base=bool(ns.require_base),
        )

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="suppression_ratchet",
            description="count lint/type suppressions and enforce a ratchet",
        )
        parser.add_argument("src", help="file or directory to scan")
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--check", action="store_true", help="ratchet check")
        action.add_argument("--update", action="store_true", help="update baseline")
        action.add_argument("--threshold", action="store_true", help="per-file table")
        action.add_argument("--json", action="store_true", help="emit JSON counts")
        parser.add_argument("--base-ref", metavar="REF", help="comparison base commit")
        parser.add_argument(
            "--require-base", action="store_true", help="fail if base unresolvable"
        )
        return parser


class Cli:
    """Scan the target and route the request to the ratchet or a view."""

    _opts: Options

    def __new__(cls, options: Options) -> Self:
        self = super().__new__(cls)
        self._opts = options
        return self

    def run(self) -> int:
        """Execute the requested operation and return its exit code."""
        if not self._opts.src.exists():
            return self._emit(Outcome.failed(f"not found: {self._opts.src}"))
        report = Scanner(self._opts.src).report
        baseline = SuppressionBaseline()
        try:
            outcome = self._dispatch(baseline, report)
        except (GitError, SuppressionBaselineError) as exc:
            outcome = Outcome.failed(f"FAIL: {exc}")
        return self._emit(outcome)

    def _dispatch(
        self, baseline: SuppressionBaseline, report: SuppressionReport
    ) -> Outcome:
        opts = self._opts
        if opts.check:
            return baseline.check(
                report, base_ref=opts.base_ref, require_base=opts.require_base
            )
        if opts.update:
            return baseline.update(report)
        if opts.json:
            return Outcome.passed(report.to_json())
        lines = list(report.render())
        if opts.threshold:
            lines.extend(report.render_threshold())
        return Outcome.passed(*lines)

    @staticmethod
    def _emit(outcome: Outcome) -> int:
        for line in outcome.lines:
            print(line)
        return outcome.exit_code


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the suppression ratchet CLI, returning a code."""
    return Cli(Options.parse(argv)).run()
