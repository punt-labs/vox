"""Suppression baseline: persistence, ratchet check, and audit logging."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import ClassVar, Self

from .outcome import Outcome
from .report import SuppressionReport


class SuppressionBaseline:
    """Persist suppression counts and refuse any increase against the baseline."""

    _baseline_path: Path
    _audit_path: Path

    BASELINE_FILE: ClassVar[str] = ".suppression-baseline.json"
    AUDIT_FILE: ClassVar[str] = ".suppression-audit.jsonl"

    def __new__(cls, root: Path | None = None) -> Self:
        self = super().__new__(cls)
        base = root if root is not None else Path.cwd()
        self._baseline_path = base / cls.BASELINE_FILE
        self._audit_path = base / cls.AUDIT_FILE
        return self

    @property
    def has_baseline(self) -> bool:
        """Return whether a baseline file exists on disk."""
        return self._baseline_path.exists()

    def check(self, report: SuppressionReport) -> Outcome:
        """Compare current counts against the baseline total."""
        if not self.has_baseline:
            return Outcome.passed("No baseline -- run --update to create one")
        data = self._load()
        baseline_total = self._as_int(data.get("total", 0))
        current_total = report.total
        head = [
            f"\nBaseline total: {baseline_total}",
            f"Current total:  {current_total}",
        ]
        if current_total > baseline_total:
            return Outcome(
                1, tuple(head + self._regression(data, report, baseline_total))
            )
        if current_total < baseline_total:
            drop = baseline_total - current_total
            return Outcome.passed(
                *head, f"\nPASS: suppression count decreased by {drop}"
            )
        return Outcome.passed(*head, "\nPASS: suppression count unchanged")

    def update(self, report: SuppressionReport) -> Outcome:
        """Write current counts to the baseline and append an audit entry."""
        self._save(report)
        self._append_audit(report)
        lines = [
            f"\nBaseline updated: {self._baseline_path}",
            f"  total: {report.total}",
        ]
        lines.extend(
            f"  {category}: {count}"
            for category, count in sorted(report.by_category.items())
        )
        return Outcome.passed(*lines)

    def _regression(
        self,
        data: dict[str, object],
        report: SuppressionReport,
        baseline_total: int,
    ) -> list[str]:
        diff = report.total - baseline_total
        lines = [
            f"\nFAIL: suppression count increased by {diff}",
            "\nFiles with new or increased suppressions:",
        ]
        baseline_by_file = self._baseline_by_file(data)
        current_by_file = report.by_file
        for fpath in sorted(set(current_by_file) | set(baseline_by_file)):
            cur = sum(current_by_file.get(fpath, {}).values())
            base = sum(baseline_by_file.get(fpath, {}).values())
            if cur > base:
                lines.append(f"  {fpath}: +{cur - base} ({base} -> {cur})")
        return lines

    @staticmethod
    def _baseline_by_file(data: dict[str, object]) -> dict[str, dict[str, int]]:
        raw = data.get("by_file", {})
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _as_int(raw: object) -> int:
        return int(raw) if isinstance(raw, (int, float)) else 0

    def _load(self) -> dict[str, object]:
        if not self._baseline_path.exists():
            return {}
        raw = json.loads(self._baseline_path.read_text())
        return dict(raw)

    def _save(self, report: SuppressionReport) -> None:
        data = {
            "total": report.total,
            "by_category": report.by_category,
            "by_file": report.by_file,
            "updated_at": self._now(),
        }
        self._baseline_path.write_text(json.dumps(data, indent=2) + "\n")

    def _append_audit(self, report: SuppressionReport) -> None:
        entry = {
            "ts": self._now(),
            "total": report.total,
            "by_category": report.by_category,
        }
        with self._audit_path.open("a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
