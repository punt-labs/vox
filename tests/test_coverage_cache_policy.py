"""Exercise the workflow's coverage-baseline compatibility check."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/test.yml"
POLICY = textwrap.dedent(
    WORKFLOW.read_text()
    .split('uv run python - "$BASE" >> "$GITHUB_OUTPUT" <<\'PY\'\n', 1)[1]
    .split("\n          PY", 1)[0]
)


@pytest.mark.subprocess
@pytest.mark.parametrize(
    ("head_config", "head_harness", "expected"),
    [
        ("[tool.coverage.run]\nbranch = true\n", "original", "reuse=true"),
        ("[tool.coverage.run]\nbranch = false\n", "original", "reuse=false"),
        ("[tool.coverage.run]\nbranch = true\n", "changed", "reuse=false"),
        (
            '[tool.coverage.run]\nbranch = true\n[project]\nname = "new"\n',
            "original",
            "reuse=true",
        ),
        ("", "original", "reuse=false"),
        ("not valid toml", "original", None),
    ],
)
def test_baseline_reuse(
    tmp_path: Path, head_config: str, head_harness: str, expected: str | None
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "--quiet")
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.coverage.run]\nbranch = true\n")
    harness = tmp_path / "tools" / "run_coverage.py"
    harness.parent.mkdir()
    harness.write_text("original")
    git("add", "pyproject.toml", "tools/run_coverage.py")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD")
    config.write_text(head_config)
    harness.write_text(head_harness)
    git("add", "pyproject.toml", "tools/run_coverage.py")
    git("commit", "--quiet", "--allow-empty", "-m", "head")

    result = subprocess.run(
        [sys.executable, "-c", POLICY, base],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    if expected is None:
        assert result.returncode != 0
        assert "TOMLDecodeError" in result.stderr
        assert not result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
