"""Integration tests for the merge-base ratchet against real tmp git repos.

Covers the load-bearing behaviors: merge-base base-compare (incl. the
concurrent-main-advance case), scoped update never loosening, the relax
waiver, rename carry, completeness enumeration, the CI-write guard, and the
bootstrap fallback.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.oo_ratchet.baseline import Baseline
from tools.oo_ratchet.gitio import GitError, GitRepo
from tools.oo_ratchet.ratchet import Ratchet
from tools.oo_ratchet.scorer import Scorer
from tools.oo_ratchet.writer import BaselineWriter

GOOD = '''from __future__ import annotations


class Widget:
    """A widget."""

    _n: int

    def __new__(cls, n: int) -> "Widget":
        self = super().__new__(cls)
        self._n = n
        return self

    def label(self) -> str:
        return "pos"
'''

WORSE = '''from __future__ import annotations


class Widget:
    """A widget."""

    _n: int

    def __new__(cls, n: int) -> "Widget":
        self = super().__new__(cls)
        self._n = n
        return self

    def label(self) -> str:
        if self._n > 0:
            if self._n > 10:
                return "big"
            return "pos"
        return "neg"
'''

BROKEN = "def oops(:\n    pass\n"


class GitFixture:
    """A throwaway git repo for exercising the ratchet end to end."""

    root: Path

    def __init__(self, tmp: Path) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
        for key, val in (
            ("user.email", "t@example.com"),
            ("user.name", "Tester"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(["git", "config", key, val], cwd=tmp, check=True)
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=tmp,
            check=True,
            capture_output=True,
            text=True,
        )
        self.root = Path(out.stdout.strip())

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def write_baseline(self, data: dict[str, dict[str, float]]) -> None:
        (self.root / ".oo-baseline.json").write_text(json.dumps(data, indent=2) + "\n")

    def snapshot(self, subdir: str) -> None:
        scorer = Scorer(self.root / subdir, self.root)
        self.write_baseline(Baseline.metrics_by_file(scorer.results))

    def commit(self, msg: str) -> str:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=self.root, check=True)
        return self._head()

    def move(self, src: str, dst: str) -> None:
        (self.root / dst).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", src, dst], cwd=self.root, check=True)

    def checkout_new(self, branch: str) -> None:
        subprocess.run(
            ["git", "checkout", "-q", "-b", branch], cwd=self.root, check=True
        )

    def checkout(self, branch: str) -> None:
        subprocess.run(["git", "checkout", "-q", branch], cwd=self.root, check=True)

    def set_origin_main(self, sha: str) -> None:
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", sha],
            cwd=self.root,
            check=True,
        )

    def _head(self) -> str:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    def ratchet(self) -> Ratchet:
        return Ratchet(self.root, GitRepo(self.root))

    def writer(self) -> BaselineWriter:
        return BaselineWriter(self.root, GitRepo(self.root))

    def scorer(self, subdir: str = "sub") -> Scorer:
        return Scorer(self.root / subdir, self.root)


@pytest.fixture
def fx(tmp_path: Path) -> GitFixture:
    return GitFixture(tmp_path)


class TestBaseCompare:
    """Compare HEAD against the baseline committed at the base commit."""

    def test_improvement_passes(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", WORSE)
        fx.snapshot("sub")
        base = fx.commit("base")
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        fx.commit("improve")
        outcome = fx.ratchet().check(fx.scorer(), base_ref=base, require_base=True)
        assert outcome.exit_code == 0

    def test_regression_fails(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        base = fx.commit("base")
        fx.write("sub/w.py", WORSE)
        fx.snapshot("sub")
        fx.commit("regress")
        outcome = fx.ratchet().check(fx.scorer(), base_ref=base, require_base=True)
        assert outcome.exit_code == 1
        assert any("regression" in line for line in outcome.lines)

    def test_merge_base_scopes_out_concurrent_main_change(self, fx: GitFixture) -> None:
        # gvr R1: a file regressed only on main is not in the PR's touched set.
        fx.write("sub/a.py", WORSE)
        fx.write("sub/b.py", GOOD)
        fx.snapshot("sub")
        fx.commit("fork")
        fx.checkout_new("feature")
        fx.write("sub/a.py", GOOD)  # PR improves a.py only
        fx.snapshot("sub")
        fx.commit("improve a")
        fx.checkout("main")
        fx.write("sub/b.py", WORSE)  # main regresses b.py concurrently
        fx.snapshot("sub")
        main_head = fx.commit("regress b on main")
        fx.set_origin_main(main_head)
        fx.checkout("feature")
        # Default base resolution uses merge-base(origin/main, HEAD) == fork.
        outcome = fx.ratchet().check(fx.scorer(), base_ref=None, require_base=True)
        assert outcome.exit_code == 0
        assert fx.root  # b.py's main-only regression did not fail the PR


class TestScopedUpdate:
    """Scoped update writes improvements but never loosens."""

    def test_update_refuses_regression(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        base = fx.commit("base")
        fx.write("sub/w.py", WORSE)  # regressed vs in-tree baseline
        outcome = fx.writer().update(
            fx.scorer(), base_ref=base, allow_ci_write=True, source=None
        )
        assert outcome.exit_code == 1
        # baseline still holds the good (unloosened) value
        entry = Baseline(fx.root).get("sub/w.py")
        assert entry is not None
        assert entry["max_complexity"] == 1.0

    def test_update_writes_improvement(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", WORSE)
        fx.snapshot("sub")
        base = fx.commit("base")
        fx.write("sub/w.py", GOOD)
        outcome = fx.writer().update(
            fx.scorer(), base_ref=base, allow_ci_write=True, source=None
        )
        assert outcome.exit_code == 0
        entry = Baseline(fx.root).get("sub/w.py")
        assert entry is not None
        assert entry["max_complexity"] == 1.0


class TestRelaxWaiver:
    """A relaxed, locked regression is waived by check."""

    def test_relax_then_check_waives(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        base = fx.commit("base")
        fx.write("sub/w.py", WORSE)  # regresses vs base
        relax = fx.writer().relax(
            fx.scorer(),
            "sub/w.py",
            justify="accepted debt",
            allow_ci_write=True,
            source="vox-x #1",
        )
        assert relax.exit_code == 0
        fx.commit("relax")
        outcome = fx.ratchet().check(fx.scorer(), base_ref=base, require_base=True)
        assert outcome.exit_code == 0

    def test_relax_requires_justification(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        fx.commit("base")
        outcome = fx.writer().relax(
            fx.scorer(), "sub/w.py", justify="  ", allow_ci_write=True, source=None
        )
        assert outcome.exit_code == 1


class TestRenameCarry:
    """A renamed file inherits its predecessor's base entry (S8)."""

    def test_rename_regression_is_caught(self, fx: GitFixture) -> None:
        fx.write("sub/old.py", GOOD)
        fx.snapshot("sub")
        base = fx.commit("base")
        fx.move("sub/old.py", "sub/new.py")
        fx.write("sub/new.py", WORSE)  # worsened during rename
        # Lock the in-tree baseline to the current (worse) value so the only
        # possible failure is the vs-base regression carried from old.py.
        scorer = fx.scorer()
        fx.write_baseline(Baseline.metrics_by_file(scorer.results))
        fx.commit("rename and worsen")
        outcome = fx.ratchet().check(fx.scorer(), base_ref=base, require_base=True)
        assert outcome.exit_code == 1
        joined = "\n".join(outcome.lines)
        assert "regression" in joined
        assert "not in baseline" not in joined


class TestCompleteness:
    """Whole-tree completeness enumerates from the scorer's own file set."""

    def test_missing_file_flagged_hidden_and_broken_excluded(
        self, fx: GitFixture
    ) -> None:
        fx.write("sub/good.py", GOOD)
        fx.write("sub/.hidden.py", WORSE)
        fx.write("sub/broken.py", BROKEN)
        fx.write_baseline({})
        outcome = fx.ratchet().audit_completeness(fx.scorer())
        assert outcome.exit_code == 1
        joined = "\n".join(outcome.lines)
        assert "sub/good.py" in joined
        assert ".hidden.py" not in joined
        assert "broken.py" not in joined

    def test_complete_baseline_passes(self, fx: GitFixture) -> None:
        fx.write("sub/good.py", GOOD)
        fx.snapshot("sub")
        outcome = fx.ratchet().audit_completeness(fx.scorer())
        assert outcome.exit_code == 0


class TestCiWriteGuard:
    """Mutations refuse to run under GITHUB_ACTIONS without --allow-ci-write."""

    def test_update_blocked_in_ci(
        self, fx: GitFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        base = fx.commit("base")
        outcome = fx.writer().update(
            fx.scorer(), base_ref=base, allow_ci_write=False, source=None
        )
        assert outcome.exit_code == 1
        assert any("GITHUB_ACTIONS" in line for line in outcome.lines)

    def test_update_allowed_with_flag_in_ci(
        self, fx: GitFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        base = fx.commit("base")
        outcome = fx.writer().update(
            fx.scorer(), base_ref=base, allow_ci_write=True, source=None
        )
        assert outcome.exit_code == 0


class TestGitFailClosed:
    """A failed git command fails closed — never a silent empty diff."""

    def test_diff_failure_raises(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        fx.commit("base")
        with pytest.raises(GitError):
            GitRepo(fx.root).diff("refs/does/not/exist")

    def test_empty_diff_is_not_a_failure(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        head = fx.commit("base")
        diff = GitRepo(fx.root).diff(head)  # HEAD vs work tree: no changes
        assert diff.touched == frozenset()


class TestBootstrap:
    """Base resolution and the O2 bootstrap / require-base rules."""

    def test_missing_base_baseline_is_bootstrap_pass(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)  # no .oo-baseline.json committed
        base = fx.commit("base without baseline")
        fx.write("sub/w.py", WORSE)
        fx.commit("change")
        outcome = fx.ratchet().check(fx.scorer(), base_ref=base, require_base=True)
        assert outcome.exit_code == 0

    def test_unresolvable_base_with_require_fails(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        fx.commit("base")
        outcome = fx.ratchet().check(fx.scorer(), base_ref="0" * 40, require_base=True)
        assert outcome.exit_code == 1

    def test_unresolvable_base_without_require_passes(self, fx: GitFixture) -> None:
        fx.write("sub/w.py", GOOD)
        fx.snapshot("sub")
        fx.commit("base")
        outcome = fx.ratchet().check(fx.scorer(), base_ref="0" * 40, require_base=False)
        assert outcome.exit_code == 0
