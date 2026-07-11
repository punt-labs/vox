"""Suppression baseline: persistence, ratchet check, and audit logging."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import ClassVar, Self

from .gitio import GitRepo
from .outcome import Outcome
from .report import SuppressionReport


class SuppressionBaselineError(Exception):
    """The in-tree ``.suppression-baseline.json`` could not be parsed.

    Raised instead of letting ``json.JSONDecodeError`` escape, so a corrupt or
    hand-broken baseline becomes a controlled non-zero outcome (the CLI catches
    it) rather than a traceback out of the gate.
    """


class SuppressionBaseline:
    """Persist suppression counts and refuse any increase against the baseline.

    The comparison baseline is read from the base commit
    (``git show <base>:.suppression-baseline.json``), not the worktree file, so a
    PR cannot launder a rising count by hand-editing the in-tree baseline. The
    in-tree file is used only on the local, no-base path (never ``--require-base``).
    """

    _baseline_path: Path
    _audit_path: Path
    _git: GitRepo

    BASELINE_FILE: ClassVar[str] = ".suppression-baseline.json"
    AUDIT_FILE: ClassVar[str] = ".suppression-audit.jsonl"

    def __new__(cls, root: Path | None = None) -> Self:
        self = super().__new__(cls)
        base = root if root is not None else Path.cwd()
        self._baseline_path = base / cls.BASELINE_FILE
        self._audit_path = base / cls.AUDIT_FILE
        self._git = GitRepo(base)
        return self

    @property
    def has_baseline(self) -> bool:
        """Return whether a baseline file exists on disk."""
        return self._baseline_path.exists()

    def check(
        self, report: SuppressionReport, *, base_ref: str | None, require_base: bool
    ) -> Outcome:
        """Compare current counts against the base-commit suppression baseline."""
        base = self._git.resolve_base(base_ref)
        if base is None:
            return self._check_local(report, require_base=require_base)
        base_data = self._git.show_baseline(base)
        if base_data is None:
            return self._absent_base(require_base=require_base)
        return self._compare(report, base_data)

    def _check_local(self, report: SuppressionReport, *, require_base: bool) -> Outcome:
        """Fail closed under --require-base; else compare vs the in-tree baseline."""
        if require_base:
            return Outcome.failed(
                "FAIL: base ref unresolvable and --require-base is set"
            )
        if not self.has_baseline:
            return Outcome.passed("No baseline -- run --update to create one")
        return self._compare(report, self._load())

    def _absent_base(self, *, require_base: bool) -> Outcome:
        """Decide the verdict when the base commit carries no baseline blob."""
        tip = self._git.resolve_ref("origin/main")
        if tip is None:
            if require_base and self.has_baseline:
                return Outcome.failed(
                    "FAIL: base has no suppression baseline and origin/main is "
                    "unresolvable with an in-tree baseline present; "
                    "fetch origin/main"
                )
            return Outcome.passed(
                "No base suppression baseline and no origin/main -- bootstrap pass"
            )
        if self._git.show_baseline(tip) is not None:
            return Outcome.failed(
                "FAIL: base commit predates suppression baseline adoption; "
                "rebase onto current main"
            )
        return Outcome.passed(
            "No suppression baseline at base or origin/main tip -- bootstrap pass"
        )

    def _compare(self, report: SuppressionReport, data: dict[str, object]) -> Outcome:
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
        try:
            raw = json.loads(self._baseline_path.read_text())
        except json.JSONDecodeError as exc:
            msg = f"corrupt suppression baseline file {self._baseline_path}: {exc}"
            raise SuppressionBaselineError(msg) from exc
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
