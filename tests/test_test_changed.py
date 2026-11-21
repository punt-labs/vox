"""Exercise changed-module selection and its git/pytest boundaries."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.test_changed import changed_files, imports_module, main, select_tests


@pytest.mark.parametrize(
    "source",
    [
        "import punt_vox.core",
        "import punt_vox.core as core",
        "from punt_vox.core import TTSClient",
        "from punt_vox import core",
        "from punt_vox import (\n core as c,\n config,\n)",
    ],
)
def test_matches_import_forms(source: str) -> None:
    assert imports_module(source, {"punt_vox.core"})


@pytest.mark.parametrize(
    "source", ["", "import punt_vox.core_extra", "from other import core"]
)
def test_ignores_unrelated_imports(source: str) -> None:
    assert not imports_module(source, {"punt_vox.core"})


def test_bad_python_fails_closed() -> None:
    with pytest.raises(SyntaxError):
        imports_module("from (", {"punt_vox.core"})


def test_selects_changed_tests_and_importers(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text("from punt_vox import core\n")
    (tests / "test_other.py").touch()
    assert select_tests(
        tmp_path,
        ["src/punt_vox/core.py", "tests/test_other.py", "tests/test_deleted.py"],
    ) == ["tests/test_core.py", "tests/test_other.py"]


@pytest.mark.parametrize(
    "changed",
    [
        "src/punt_vox/__init__.py",
        "src/punt_vox/voxd/__init__.py",
        "tests/conftest.py",
        "tests/e2e/conftest.py",
    ],
)
def test_shared_configuration_selects_full_tier(tmp_path: Path, changed: str) -> None:
    assert select_tests(tmp_path, [changed]) is None


def test_no_matching_changes(tmp_path: Path) -> None:
    assert select_tests(tmp_path, ["README.md", "tools/run_metrics.py"]) == []


@pytest.mark.parametrize("ref", [None, "origin/main"])
def test_git_diff_is_nul_delimited(
    monkeypatch: pytest.MonkeyPatch, ref: str | None
) -> None:
    output = MagicMock(return_value=b"tests/test_one two.py\0src/punt_vox/core.py\0")
    monkeypatch.setattr("tools.test_changed.subprocess.check_output", output)
    assert changed_files(ref) == ["tests/test_one two.py", "src/punt_vox/core.py"]
    command = output.call_args.args[0]
    assert ("--cached" if ref is None else "origin/main...HEAD") in command
    assert "-z" in command
    assert "--no-renames" in command


def test_git_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.test_changed.subprocess.check_output",
        MagicMock(side_effect=subprocess.CalledProcessError(128, "git")),
    )
    with pytest.raises(subprocess.CalledProcessError):
        changed_files("bad-ref")


@pytest.mark.parametrize("selected", [[], None, ["tests/test_core.py"]])
@pytest.mark.parametrize("status", [0, 1, 5])
def test_cli_runs_only_selected_tests(
    monkeypatch: pytest.MonkeyPatch, selected: list[str] | None, status: int
) -> None:
    monkeypatch.setattr("sys.argv", ["test_changed", "--ref", "main"])
    changes = MagicMock(return_value=[])
    monkeypatch.setattr("tools.test_changed.changed_files", changes)
    monkeypatch.setattr(
        "tools.test_changed.select_tests", MagicMock(return_value=selected)
    )
    run = MagicMock(return_value=subprocess.CompletedProcess([], status))
    monkeypatch.setattr("tools.test_changed.subprocess.run", run)
    assert main() == (0 if selected == [] or status == 5 else status)
    changes.assert_called_once_with("main")
    if selected == []:
        run.assert_not_called()
    else:
        assert run.call_args.args[0][1:] == [
            "-m",
            "pytest",
            "-n",
            "auto",
            *(selected or []),
        ]
