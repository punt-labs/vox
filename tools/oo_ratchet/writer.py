"""Baseline mutations: scoped update, whole-tree reconcile, relax, rebaseline.

Every mutation refuses per-metric regressions except ``relax`` (the single
sanctioned, audited loosening) and ``rebaseline`` (an explicit structural
reset). All refuse to run under ``GITHUB_ACTIONS`` unless ``--allow-ci-write``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from .audit import AuditLog
from .baseline import Baseline
from .gitio import GitRepo
from .outcome import Outcome
from .scorer import Scorer
from .thresholds import Thresholds


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    """Which files an update touches, plus rename and prune intent."""

    current: dict[str, dict[str, float]]
    touched: frozenset[str]
    renames: dict[str, str] = field(default_factory=dict)
    prune: bool = False


class BaselineWriter:
    """Apply never-loosening baseline updates and audited relaxations."""

    _baseline: Baseline
    _audit: AuditLog
    _git: GitRepo

    def __new__(cls, root: Path, git: GitRepo) -> Self:
        self = super().__new__(cls)
        self._baseline = Baseline(root)
        self._audit = AuditLog(root)
        self._git = git
        return self

    def update(
        self,
        scorer: Scorer,
        *,
        base_ref: str | None,
        allow_ci_write: bool,
        source: str | None,
    ) -> Outcome:
        """Update baseline entries for files in ``base..HEAD`` (never loosens)."""
        blocked = self._guard(allow_ci_write=allow_ci_write)
        if blocked is not None:
            return blocked
        current = Baseline.metrics_by_file(scorer.results)
        base = self._git.resolve_base(base_ref)
        if base is None:
            plan = UpdatePlan(current, frozenset(current))
        else:
            diff = self._git.diff(base)
            plan = UpdatePlan(current, diff.python_files(), diff.renames)
        return self._apply(plan, source)

    def reconcile(
        self, scorer: Scorer, *, allow_ci_write: bool, source: str | None
    ) -> Outcome:
        """Sweep the whole tree, adding improvements and pruning deletions."""
        blocked = self._guard(allow_ci_write=allow_ci_write)
        if blocked is not None:
            return blocked
        current = Baseline.metrics_by_file(scorer.results)
        plan = UpdatePlan(current, frozenset(current), prune=True)
        return self._apply(plan, source)

    def relax(
        self,
        scorer: Scorer,
        file: str,
        *,
        justify: str,
        allow_ci_write: bool,
        source: str | None,
    ) -> Outcome:
        """Write ``file``'s current metrics even if looser, with justification."""
        blocked = self._guard(allow_ci_write=allow_ci_write)
        if blocked is not None:
            return blocked
        if not justify.strip():
            return Outcome.failed("FAIL: --relax requires a non-empty --justify")
        current = Baseline.metrics_by_file(scorer.results)
        entry = current.get(file)
        if entry is None:
            return Outcome.failed(f"FAIL: not a scored file: {file}")
        base_entry = self._baseline.get(file) or {}
        # Record ONLY the metrics this relaxation actually loosened (worse than
        # the pre-relax baseline). A metric that held or improved is not waivable
        # -- otherwise relaxing M1 would silently bless a future regression of an
        # untouched M2 on the same file.
        loosened = self._regressed(entry, base_entry)
        deltas = {file: {m: [base_entry.get(m, entry[m]), entry[m]] for m in loosened}}
        new_baseline = dict(self._baseline.entries)
        new_baseline[file] = entry
        self._baseline.save(new_baseline)
        self._audit.append(
            files_scored=len(current),
            files_improved=0,
            files_regressed=1,
            verdict="relaxed",
            deltas=deltas,
            source=source,
            commit=self._git.short_head(),
            reason=justify,
        )
        return Outcome.passed(
            f"\nRelaxed {file} (reason: {justify})",
            f"  baseline: {self._baseline.path}",
        )

    def rebaseline(
        self, scorer: Scorer, *, allow_ci_write: bool, source: str | None
    ) -> Outcome:
        """Reset the baseline unconditionally to current scores."""
        blocked = self._guard(allow_ci_write=allow_ci_write)
        if blocked is not None:
            return blocked
        current = Baseline.metrics_by_file(scorer.results)
        self._baseline.save(current)
        self._audit.append(
            files_scored=len(current),
            files_improved=0,
            files_regressed=0,
            verdict="rebaseline",
            deltas={},
            source=source,
            commit=self._git.short_head(),
        )
        return Outcome.passed(
            f"\nBaseline reset: {self._baseline.path}",
            f"  files scored: {len(current)}",
        )

    @staticmethod
    def _guard(*, allow_ci_write: bool) -> Outcome | None:
        if os.environ.get("GITHUB_ACTIONS") == "true" and not allow_ci_write:
            return Outcome.failed(
                "FAIL: refusing to write baseline under GITHUB_ACTIONS "
                "without --allow-ci-write"
            )
        return None

    def _apply(self, plan: UpdatePlan, source: str | None) -> Outcome:
        new_baseline = dict(self._baseline.entries)
        refused: list[tuple[str, str]] = []
        deltas: dict[str, dict[str, list[float]]] = {}
        removed = 0
        for path in sorted(plan.touched):
            removed += self._drop_rename_source(new_baseline, plan, path)
            if path not in plan.current:
                removed += self._drop(new_baseline, path)
                continue
            self._write_or_refuse(new_baseline, plan, path, refused, deltas)
        if plan.prune:
            removed += self._prune(new_baseline, plan.current)
        self._baseline.save(new_baseline)
        self._audit.append(
            files_scored=len(plan.current),
            files_improved=len(deltas),
            files_regressed=len({p for p, _ in refused}),
            verdict="pass" if not refused else "fail",
            deltas=deltas,
            source=source,
            commit=self._git.short_head(),
        )
        return self._report(len(deltas), removed, refused)

    def _write_or_refuse(
        self,
        new_baseline: dict[str, dict[str, float]],
        plan: UpdatePlan,
        path: str,
        refused: list[tuple[str, str]],
        deltas: dict[str, dict[str, list[float]]],
    ) -> None:
        entry = plan.current[path]
        base_entry = self._base_for(plan, path)
        regressed = self._regressed(entry, base_entry)
        if regressed:
            refused.extend((path, m) for m in regressed)
            return
        new_baseline[path] = entry
        file_deltas = self._deltas(entry, base_entry)
        if file_deltas:
            deltas[path] = file_deltas

    def _base_for(self, plan: UpdatePlan, path: str) -> dict[str, float] | None:
        """Return the regression base, carrying a rename's old entry (S8).

        A renamed file is compared against its predecessor's baseline so a
        rename cannot launder a regressed metric past as a brand-new file.
        """
        base_entry = self._baseline.get(path)
        if base_entry is None and path in plan.renames:
            return self._baseline.get(plan.renames[path])
        return base_entry

    def _drop_rename_source(
        self, new_baseline: dict[str, dict[str, float]], plan: UpdatePlan, path: str
    ) -> int:
        old = plan.renames.get(path)
        if old is not None:
            return self._drop(new_baseline, old)
        return 0

    @staticmethod
    def _drop(new_baseline: dict[str, dict[str, float]], path: str) -> int:
        if path in new_baseline:
            del new_baseline[path]
            return 1
        return 0

    @staticmethod
    def _prune(
        new_baseline: dict[str, dict[str, float]], current: dict[str, dict[str, float]]
    ) -> int:
        stale = [p for p in new_baseline if p not in current]
        for p in stale:
            del new_baseline[p]
        return len(stale)

    @staticmethod
    def _regressed(entry: dict[str, float], base: dict[str, float] | None) -> list[str]:
        if base is None:
            return []
        return [
            m
            for m in entry
            if m in base and not Thresholds.better_or_equal(m, entry[m], base[m])
        ]

    @staticmethod
    def _deltas(
        entry: dict[str, float], base: dict[str, float] | None
    ) -> dict[str, list[float]]:
        if base is None:
            return {m: [0.0, entry[m]] for m in entry}
        return {
            m: [base[m], entry[m]] for m in entry if m in base and entry[m] != base[m]
        }

    def _report(
        self, improved: int, removed: int, refused: list[tuple[str, str]]
    ) -> Outcome:
        lines = [
            f"\nBaseline updated: {self._baseline.path}",
            f"  files improved: {improved}",
            f"  files removed:  {removed}",
        ]
        if refused:
            lines.append(f"  REFUSED ({len({p for p, _ in refused})} files):")
            lines.extend(f"    {p}: {m} regressed" for p, m in refused)
            return Outcome(1, tuple(lines))
        return Outcome(0, tuple(lines))
