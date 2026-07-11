"""Behavior tests for the decomposed suppression counter and ratchet.

The decomposition is behavior-preserving: these lock the counting semantics
(code-line detection, category totals), the ratchet verdict (increase fails,
steady/decrease passes), and the CLI dispatch through tmp files.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Self

from tools.suppression.baseline import SuppressionBaseline
from tools.suppression.patterns import FileSuppressions
from tools.suppression.report import SuppressionReport
from tools.suppression.scanner import Scanner

if TYPE_CHECKING:
    import pytest

WITH_SUPPRESSIONS = (
    "from __future__ import annotations\n\n"
    "x = 1  # noqa: E501\n"
    "y = 2  # type: ignore[assignment]\n"
    "z = 3  # pylint: disable=invalid-name\n"
)

# A multiline docstring interior and a bare comment line -- both excluded from
# the code-line scan, so their `# noqa` markers must not be counted.
DOCSTRING_AND_COMMENT = '"""\n# noqa\ndocstring body\n"""\n\n# noqa\nvalue = 1\n'


class TestFileSuppressions:
    """Count suppression comments on code lines only."""

    def test_counts_code_line_suppressions(self) -> None:
        fs = FileSuppressions("m.py", WITH_SUPPRESSIONS)
        assert fs.count("noqa") == 1
        assert fs.count("type_ignore") == 1
        assert fs.count("pylint_disable") == 1
        assert fs.total == 3

    def test_docstring_and_comment_lines_excluded(self) -> None:
        assert FileSuppressions("m.py", DOCSTRING_AND_COMMENT).total == 0


class TestScanner:
    """Aggregate per-file counts into a report."""

    def test_scans_directory(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text(WITH_SUPPRESSIONS)
        report = Scanner(tmp_path / "pkg", tmp_path).report
        assert report.by_category["noqa"] == 1
        assert report.by_category["type_ignore"] == 1
        assert report.total >= 2


class Fixture:
    """A tmp project root for exercising the suppression baseline."""

    _root: Path

    def __new__(cls, root: Path) -> Self:
        self = super().__new__(cls)
        self._root = root
        return self

    @property
    def root(self) -> Path:
        """Return the project root."""
        return self._root

    def report(self, source: str) -> SuppressionReport:
        (self._root / "pkg").mkdir(exist_ok=True)
        (self._root / "pkg" / "a.py").write_text(source)
        return Scanner(self._root / "pkg", self._root).report


class TestBaselineRatchet:
    """Increases fail; steady and decreases pass."""

    def test_no_baseline_passes(self, tmp_path: Path) -> None:
        report = Fixture(tmp_path).report(WITH_SUPPRESSIONS)
        outcome = SuppressionBaseline(tmp_path).check(report)
        assert outcome.exit_code == 0

    def test_increase_fails(self, tmp_path: Path) -> None:
        fx = Fixture(tmp_path)
        SuppressionBaseline(tmp_path).update(fx.report("x = 1  # noqa\n"))
        worse = fx.report("x = 1  # noqa\ny = 2  # noqa\n")
        outcome = SuppressionBaseline(tmp_path).check(worse)
        assert outcome.exit_code == 1
        assert any("increased" in line for line in outcome.lines)

    def test_decrease_passes(self, tmp_path: Path) -> None:
        fx = Fixture(tmp_path)
        worse = fx.report("x = 1  # noqa\ny = 2  # noqa\n")
        SuppressionBaseline(tmp_path).update(worse)
        better = fx.report("x = 1  # noqa\n")
        outcome = SuppressionBaseline(tmp_path).check(better)
        assert outcome.exit_code == 0
        assert any("decreased" in line for line in outcome.lines)

    def test_steady_passes(self, tmp_path: Path) -> None:
        fx = Fixture(tmp_path)
        SuppressionBaseline(tmp_path).update(fx.report("x = 1  # noqa\n"))
        outcome = SuppressionBaseline(tmp_path).check(fx.report("x = 1  # noqa\n"))
        assert outcome.exit_code == 0
        assert any("unchanged" in line for line in outcome.lines)


def test_cli_json_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.suppression.cli import main

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(WITH_SUPPRESSIONS)
    monkeypatch.chdir(tmp_path)
    assert main(["pkg", "--json"]) == 0
