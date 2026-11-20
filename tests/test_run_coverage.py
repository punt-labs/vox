"""Exercise coverage-runner startup hooks, failure propagation, and cleanup."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from tools.run_coverage import run_coverage


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, MagicMock]:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "test.whl").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "tools.run_coverage.sysconfig.get_path", MagicMock(return_value=str(tmp_path))
    )
    monkeypatch.setattr(
        "tools.run_coverage.subprocess.check_output",
        MagicMock(return_value=str(tmp_path)),
    )
    cov = MagicMock()
    monkeypatch.setattr("tools.run_coverage.coverage.Coverage", lambda: cov)
    return tmp_path, cov


@pytest.mark.parametrize("returncode", [0, 1, 5])
def test_runner_reports_and_preserves_exit(
    runner: tuple[Path, MagicMock], monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    root, cov = runner

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "pytest" in args:
            assert (
                (root / "vox_coverage.pth").read_text().startswith("import coverage;")
            )
            env = cast("dict[str, str]", kwargs["env"])
            assert env["COVERAGE_PROCESS_START"] == str(root / "pyproject.toml")
            return subprocess.CompletedProcess(args, returncode)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("tools.run_coverage.subprocess.run", run)
    assert run_coverage() == returncode
    assert not (root / "vox_coverage.pth").exists()
    cov.combine.assert_called_once_with(strict=False)
    cov.xml_report.assert_called_once_with(outfile="coverage.xml")
    cov.json_report.assert_called_once_with(outfile="coverage.json")


def test_runner_removes_hook_after_spawn_error(
    runner: tuple[Path, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = runner

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "pytest" in args:
            raise OSError("cannot spawn")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("tools.run_coverage.subprocess.run", run)
    with pytest.raises(OSError, match="cannot spawn"):
        run_coverage()
    assert not (root / "vox_coverage.pth").exists()


def test_runner_refuses_existing_hook(
    runner: tuple[Path, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = runner
    hook = root / "vox_coverage.pth"
    hook.write_text("existing")
    monkeypatch.setattr("tools.run_coverage.subprocess.run", MagicMock())
    with pytest.raises(FileExistsError):
        run_coverage()
    assert hook.read_text() == "existing"


def test_runner_missing_wheel(runner: tuple[Path, MagicMock]) -> None:
    root, _ = runner
    (root / "dist/test.whl").unlink()
    with pytest.raises(StopIteration):
        run_coverage()
