"""Unit tests for the scorer's file enumeration and path normalization."""

from __future__ import annotations

from pathlib import Path

from tools.oo_ratchet.scorer import Scorer

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

BROKEN = "def oops(:\n    pass\n"


class TestEnumeration:
    """The scorer's own file set drives completeness (S6)."""

    def test_dotfiles_and_parse_errors_excluded_from_files(
        self, tmp_path: Path
    ) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "good.py").write_text(GOOD)
        (sub / ".hidden.py").write_text(GOOD)
        (sub / "broken.py").write_text(BROKEN)
        scorer = Scorer(sub, tmp_path)
        assert scorer.files == {"sub/good.py"}

    def test_parse_error_recorded_in_results(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "broken.py").write_text(BROKEN)
        scorer = Scorer(sub, tmp_path)
        errors = [r for r in scorer.results if "error" in r]
        assert len(errors) == 1


class TestNormalization:
    """Keys are repo-relative POSIX paths regardless of target spelling."""

    def test_keys_relative_to_repo_root(self, tmp_path: Path) -> None:
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text(GOOD)
        scorer = Scorer(pkg, tmp_path)
        assert scorer.files == {"src/pkg/mod.py"}

    def test_single_file_target(self, tmp_path: Path) -> None:
        mod = tmp_path / "mod.py"
        mod.write_text(GOOD)
        scorer = Scorer(mod, tmp_path)
        assert scorer.files == {"mod.py"}
