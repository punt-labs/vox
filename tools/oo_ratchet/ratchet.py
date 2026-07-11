"""Ratchet checking: compare HEAD metrics against the merge-base baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Self

from .audit import AuditLog
from .baseline import Baseline
from .compare import FileReview, Review
from .gitio import GitRepo
from .outcome import Outcome
from .scorer import Scorer
from .thresholds import Thresholds

_HEADER = (
    f"\n{'File':<40} {'Metric':<26} {'Baseline':>10} "
    f"{'Current':>10} {'Delta':>8} {'Status':>10}"
)


class Ratchet:
    """Enforce the ratchet: HEAD metrics must not regress against the base.

    The comparison baseline is loaded from the base commit
    (``git show <base>:.oo-baseline.json``), never the in-tree file, so a PR
    may improve code and lock in its own baseline in the same commit (S2).
    """

    _baseline: Baseline
    _audit: AuditLog
    _git: GitRepo

    def __new__(cls, root: Path, git: GitRepo) -> Self:
        self = super().__new__(cls)
        self._baseline = Baseline(root)
        self._audit = AuditLog(root)
        self._git = git
        return self

    def check(
        self, scorer: Scorer, *, base_ref: str | None, require_base: bool
    ) -> Outcome:
        """Compare touched files at HEAD against the base baseline."""
        base = self._git.resolve_base(base_ref)
        if base is None:
            return self._no_base(require_base=require_base)
        base_baseline = self._git.show_baseline(base)
        if base_baseline is None:
            return Outcome.passed(
                "No base baseline to compare against -- bootstrap trivial pass"
            )

        diff = self._git.diff(base)
        touched_py = diff.python_files()
        broken = sorted(touched_py & scorer.parse_errors)
        if broken:
            lines = ["FAIL: touched file(s) failed to parse:"]
            lines.extend(f"  {path}" for path in broken)
            return Outcome(1, tuple(lines))

        current = Baseline.metrics_by_file(scorer.results)
        touched = sorted(touched_py & scorer.files)
        if not touched:
            return Outcome.passed("No Python files touched -- trivial pass")

        waivable = self._audit.relaxations_since(self._git.show_audit(base))
        reviews = self._build_reviews(
            touched, current, base_baseline, diff.renames, waivable
        )
        lock_fail, missing = self._integrity(touched, current)
        return self._verdict(Review(reviews), lock_fail, missing)

    def _no_base(self, *, require_base: bool) -> Outcome:
        """Decide the verdict when no comparison base can be resolved.

        Bootstrap-pass only in genuine first-adoption — no in-tree baseline
        either. With a baseline present, an unresolvable base means a stale or
        unfetched ``origin/main``; fail loud rather than silently no-op.
        """
        if require_base:
            return Outcome.failed(
                "FAIL: base ref unresolvable and --require-base is set"
            )
        if not self._baseline.exists:
            return Outcome.passed(
                "No base and no in-tree baseline -- first-adoption bootstrap pass"
            )
        return Outcome.failed(
            "FAIL: cannot resolve merge-base (origin/main unfetched or stale) "
            "with an in-tree baseline present; fetch origin/main or pass --base-ref"
        )

    def show_log(self) -> Outcome:
        """Return the audit history as an outcome."""
        return Outcome.passed(*self._audit.render_log())

    def audit_completeness(self, scorer: Scorer) -> Outcome:
        """Fail if any scored file is absent from the in-tree baseline (S6).

        Enumerated from the scorer's own parse-clean file set, so a dotted or
        unparseable file is never demanded in a baseline it can never have.
        """
        missing = sorted(scorer.files - set(self._baseline.entries))
        if missing:
            lines = ["FAIL: files missing from baseline:"]
            lines.extend(f"  {path}" for path in missing)
            return Outcome(1, tuple(lines))
        return Outcome.passed(
            f"PASS: baseline complete for {len(scorer.files)} scored files"
        )

    def _build_reviews(
        self,
        touched: list[str],
        current: dict[str, dict[str, float]],
        base_baseline: dict[str, dict[str, float]],
        renames: dict[str, str],
        waivable: frozenset[tuple[str, str]],
    ) -> tuple[FileReview, ...]:
        reviews: list[FileReview] = []
        for path in touched:
            base_entry = base_baseline.get(path)
            if base_entry is None and path in renames:
                base_entry = base_baseline.get(renames[path])
            reviews.append(
                FileReview(
                    path,
                    current[path],
                    base_entry,
                    self._baseline.get(path),
                    waivable,
                )
            )
        return tuple(reviews)

    def _integrity(
        self, touched: list[str], current: dict[str, dict[str, float]]
    ) -> tuple[list[str], list[str]]:
        lock_fail: list[str] = []
        missing: list[str] = []
        for path in touched:
            intree = self._baseline.get(path)
            if intree is None:
                missing.append(path)
            elif not self._locked(intree, current[path]):
                lock_fail.append(path)
        return lock_fail, missing

    @staticmethod
    def _locked(intree: dict[str, float], current: dict[str, float]) -> bool:
        return all(
            intree.get(m) == current.get(m) for m in Thresholds.names() if m in current
        )

    def _verdict(
        self, review: Review, lock_fail: list[str], missing: list[str]
    ) -> Outcome:
        lines = self._render_rows(review)
        failed = False
        if missing:
            lines.append(
                "FAIL: touched file(s) not in baseline -- run make update-oo: "
                + ", ".join(missing)
            )
            failed = True
        if lock_fail:
            lines.append(
                "FAIL: baseline out of date -- run make update-oo: "
                + ", ".join(lock_fail)
            )
            failed = True
        if review.has_regression:
            lines.append("FAIL: regression detected")
            lines.extend(f"  {path}: {metric}" for path, metric in review.regressions)
            failed = True
        if not review.improvement_satisfied:
            lines.append("FAIL: no metric improved on any touched file")
            failed = True
        if failed:
            return Outcome(1, tuple(lines))
        lines.append("PASS: at least one metric improved, no regressions")
        return Outcome(0, tuple(lines))

    @staticmethod
    def _render_rows(review: Review) -> list[str]:
        lines = [_HEADER, "-" * 108]
        lines.extend(row.render() for row in review.rows)
        if not review.rows:
            lines.append("  (all metrics unchanged)")
        return lines
