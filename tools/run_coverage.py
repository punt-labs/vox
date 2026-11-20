#!/usr/bin/env python3
"""Measure all credential-free tiers, including installed-wheel subprocesses."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import coverage


def run_coverage(rcfile: Path | None = None) -> int:
    """Install startup hooks, run pytest-cov, and combine reports."""
    root = Path.cwd()
    config_file = str((rcfile or root / "pyproject.toml").resolve())
    subprocess.run(
        [
            "uv",
            "tool",
            "install",
            "--force",
            "--with",
            f"coverage=={coverage.__version__}",
            f"{next(root.glob('dist/*.whl'))}[call]",
        ],
        check=True,
    )
    tool_dir = subprocess.check_output(["uv", "tool", "dir"], text=True).strip()
    tool_python = Path(tool_dir) / "punt-vox/bin/python"
    tool_site = subprocess.check_output(
        [
            str(tool_python),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        text=True,
    ).strip()
    hooks = [
        Path(sysconfig.get_path("purelib")) / "vox_coverage.pth",
        Path(tool_site) / "vox_coverage.pth",
    ]
    for hook in hooks:
        if hook.exists():
            raise FileExistsError(hook)
    env = dict(os.environ)
    env.pop("COVERAGE_RCFILE", None)
    env["COVERAGE_PROCESS_START"] = config_file
    env["COVERAGE_FILE"] = str(root / ".coverage")
    cov = coverage.Coverage(config_file=config_file)
    cov.erase()
    try:
        for hook in hooks:
            hook.write_text("import coverage; coverage.process_startup()\n")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "not integration and not sdk and not roundtrip",
                "--cov=punt_vox",
                f"--cov-config={config_file}",
                "--cov-report=",
            ],
            env=env,
            check=False,
        )
    finally:
        for hook in hooks:
            hook.unlink(missing_ok=True)
    cov.load()
    cov.combine(strict=False)
    cov.save()
    cov.report()
    cov.xml_report(outfile="coverage.xml")
    cov.html_report(directory="htmlcov")
    cov.json_report(outfile="coverage.json")
    return result.returncode


def main() -> int:
    """Run coverage with an explicit optional configuration path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rcfile", type=Path, help="Coverage configuration file")
    return run_coverage(parser.parse_args().rcfile)


if __name__ == "__main__":
    sys.exit(main())
