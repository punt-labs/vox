"""Coupling ratchet check: touched files must not regress against the baseline.

The touched set is scoped to the whole PR (``merge-base..worktree``), not the
last commit, so a coupling regression introduced in an earlier commit and not
re-touched in the final commit is still scored. The comparison is against the
*in-tree* baseline (regression-only); git resolves the base solely to scope the
touched set.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Self

from .audit import CouplingAudit
from .baseline import CouplingBaseline
from .compare import CouplingReview, Row
from .gitio import GitRepo
from .outcome import Outcome

if TYPE_CHECKING:
    from .scorer import CouplingScorer

_HEADER = (
    f"\n{'File':<40} {'Metric':<26} {'Baseline':>10} "
    f"{'Current':>10} {'Delta':>8} {'Status':>10}"
)


class CouplingRatchet:
    """Enforce the coupling gate: touched files must not regress vs baseline."""

    _baseline: CouplingBaseline
    _audit: CouplingAudit
    _git: GitRepo

    def __new__(cls, root: Path, git: GitRepo) -> Self:
        self = super().__new__(cls)
        self._baseline = CouplingBaseline(root)
        self._audit = CouplingAudit(root)
        self._git = git
        return self

    def check(
        self, scorer: CouplingScorer, *, base_ref: str | None, require_base: bool
    ) -> Outcome:
        """Compare PR-touched files against the in-tree coupling baseline."""
        if not self._baseline.exists:
            return self._no_baseline(require_base=require_base)
        base = self._git.resolve_base(base_ref)
        if base is None:
            return self._no_base(require_base=require_base)

        diff = self._git.diff(base)
        touched_py = diff.python_files()
        broken = sorted(touched_py & scorer.parse_errors)
        if broken:
            lines = ["FAIL: touched file(s) failed to parse:"]
            lines.extend(f"  {path}" for path in broken)
            return Outcome(1, tuple(lines))

        current = CouplingBaseline.metrics_by_file(scorer.results)
        touched = sorted(touched_py & scorer.files)
        if not touched:
            return Outcome.passed("No Python files touched -- trivial pass")

        reviews = self._build_reviews(touched, current, diff.renames)
        return self._verdict(reviews)

    def show_log(self) -> Outcome:
        """Return the audit history as an outcome."""
        return Outcome.passed(*self._audit.render_log())

    def _no_baseline(self, *, require_base: bool) -> Outcome:
        """Decide the verdict when no in-tree coupling baseline exists yet."""
        if require_base:
            return Outcome.failed(
                "FAIL: no coupling baseline and --require-base is set; "
                "run make update-coupling"
            )
        return Outcome.passed("No baseline -- run make update-coupling to create one")

    def _no_base(self, *, require_base: bool) -> Outcome:
        """Decide the verdict when no comparison base can be resolved.

        A coupling baseline is present (checked before this), so an unresolvable
        base means a stale or unfetched ``origin/main``. Fail closed rather than
        silently scoring nothing -- an empty touched set would pass every PR.
        """
        if require_base:
            return Outcome.failed(
                "FAIL: base ref unresolvable and --require-base is set"
            )
        return Outcome.failed(
            "FAIL: cannot resolve merge-base (origin/main unfetched or stale) "
            "with a coupling baseline present; fetch origin/main or pass --base-ref"
        )

    def _build_reviews(
        self,
        touched: list[str],
        current: dict[str, dict[str, float]],
        renames: dict[str, str],
    ) -> tuple[CouplingReview, ...]:
        reviews: list[CouplingReview] = []
        for path in touched:
            cur = current.get(path)
            if cur is None:
                continue
            base_entry = self._baseline.get(path)
            if base_entry is None and path in renames:
                base_entry = self._baseline.get(renames[path])
            reviews.append(CouplingReview(path, cur, base_entry))
        return tuple(reviews)

    def _verdict(self, reviews: tuple[CouplingReview, ...]) -> Outcome:
        rows = tuple(row for review in reviews for row in review.rows)
        regressions = tuple((r.path, metric) for r in reviews for metric in r.regressed)
        lines = self._render_rows(rows)
        if regressions:
            lines.append("\nFAIL: regression detected")
            lines.extend(f"  {path}: {metric}" for path, metric in regressions)
            return Outcome(1, tuple(lines))
        lines.append("\nPASS: no regressions")
        return Outcome(0, tuple(lines))

    @staticmethod
    def _render_rows(rows: tuple[Row, ...]) -> list[str]:
        lines = [_HEADER, "-" * 108]
        lines.extend(row.render() for row in rows)
        if not rows:
            lines.append("  (all metrics unchanged)")
        return lines
