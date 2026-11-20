"""Exercise coverage-runner startup hooks, failure propagation, and cleanup."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from tools.run_coverage import main, run_coverage


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
    monkeypatch.setattr(
        "tools.run_coverage.coverage.Coverage", MagicMock(return_value=cov)
    )
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
            assert "COVERAGE_RCFILE" not in env
            assert f"--cov-config={root / 'pyproject.toml'}" in args
            return subprocess.CompletedProcess(args, returncode)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("tools.run_coverage.subprocess.run", run)
    assert run_coverage() == returncode
    assert not (root / "vox_coverage.pth").exists()
    cov.combine.assert_called_once_with(strict=False)
    cov.xml_report.assert_called_once_with(outfile="coverage.xml")
    cov.json_report.assert_called_once_with(outfile="coverage.json")


@pytest.mark.parametrize("inherited", [None, "ambient.toml"])
def test_explicit_config_is_shared_without_environment_leak(
    runner: tuple[Path, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    inherited: str | None,
) -> None:
    root, cov = runner
    config = root / "coverage config.toml"
    factory = MagicMock(return_value=cov)
    monkeypatch.setattr("tools.run_coverage.coverage.Coverage", factory)
    if inherited is None:
        monkeypatch.delenv("COVERAGE_RCFILE", raising=False)
    else:
        monkeypatch.setenv("COVERAGE_RCFILE", inherited)

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "pytest" in args:
            env = cast("dict[str, str]", kwargs["env"])
            assert "COVERAGE_RCFILE" not in env
            assert env["COVERAGE_PROCESS_START"] == str(config)
            assert f"--cov-config={config}" in args
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("tools.run_coverage.subprocess.run", run)
    assert run_coverage(Path("coverage config.toml")) == 0
    factory.assert_called_once_with(config_file=str(config))
    assert not (root / "vox_coverage.pth").exists()


def test_external_config_keeps_local_test_discovery(
    runner: tuple[Path, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = runner
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_base.py").write_text("def test_base():\n    assert True\n")
    spikes = root / "spikes"
    spikes.mkdir()
    (spikes / "test_uncollected.py").write_text(
        'raise RuntimeError("spike tests must not be collected")\n'
    )
    head = root / "head"
    head.mkdir()
    config = head / "pyproject.toml"
    config.write_text('[tool.coverage.run]\nsource = ["punt_vox"]\nbranch = true\n')
    subprocess_run = subprocess.run

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "pytest" in args:
            env = cast("dict[str, str]", kwargs["env"])
            env.pop("PYTEST_ADDOPTS", None)
            return subprocess_run(args, env=env, check=False, text=True)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("tools.run_coverage.subprocess.run", run)
    assert run_coverage(config) == 0


@pytest.mark.parametrize("rcfile", [None, "head config.toml"])
def test_cli_configuration(monkeypatch: pytest.MonkeyPatch, rcfile: str | None) -> None:
    args = ["run_coverage"]
    if rcfile is not None:
        args.extend(["--rcfile", rcfile])
    monkeypatch.setattr("sys.argv", args)
    run = MagicMock(return_value=1)
    monkeypatch.setattr("tools.run_coverage.run_coverage", run)
    assert main() == 1
    run.assert_called_once_with(None if rcfile is None else Path(rcfile))


def test_cli_requires_configuration_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["run_coverage", "--rcfile"])
    run = MagicMock()
    monkeypatch.setattr("tools.run_coverage.run_coverage", run)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    run.assert_not_called()


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
