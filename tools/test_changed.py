#!/usr/bin/env python3
"""Select default-tier tests that directly import changed Python modules."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path


def changed_files(ref: str | None) -> list[str]:
    """Read staged paths, or paths changed since a reference's merge base."""
    revision = [f"{ref}...HEAD"] if ref is not None else ["--cached"]
    result = subprocess.check_output(
        [
            "git",
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            "--diff-filter=ACMRD",
            *revision,
        ],
    )
    return [name for name in result.decode().split("\0") if name]


def imports_module(source: str, modules: set[str]) -> bool:
    """Match direct imports and from-package imports without prefix collisions."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(alias.name in modules for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in modules or any(
                f"{node.module}.{alias.name}" in modules for alias in node.names
            ):
                return True
    return False


def select_tests(root: Path, changed: list[str]) -> list[str] | None:
    """Return matching test paths, or None when the full default tier is needed."""
    python = [Path(name) for name in changed if name.endswith(".py")]
    if any(
        path.name == "conftest.py"
        or (path.is_relative_to("src/punt_vox") and path.name == "__init__.py")
        for path in python
    ):
        return None
    selected = {
        str(path)
        for path in python
        if path.is_relative_to("tests")
        and path.name.startswith("test_")
        and (root / path).is_file()
    }
    modules = {
        ".".join(path.with_suffix("").parts[1:])
        for path in python
        if path.is_relative_to("src/punt_vox")
    }
    if modules:
        selected.update(
            str(path.relative_to(root))
            for path in (root / "tests").rglob("test_*.py")
            if imports_module(path.read_text(), modules)
        )
    return sorted(selected)


def main() -> int:
    """Run selected tests with pytest's default marker filter and xdist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="Compare HEAD against this merge base")
    args = parser.parse_args()
    selected = select_tests(Path.cwd(), changed_files(args.ref))
    if selected == []:
        print("No changed-module tests selected.")
        return 0
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-n", "auto", *(selected or [])],
        check=False,
    )
    return 0 if result.returncode == 5 else result.returncode


if __name__ == "__main__":
    sys.exit(main())
