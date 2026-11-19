"""Exercise the logging-source gate against real fixture source trees."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-logging-sources.py"
pytestmark = pytest.mark.subprocess


def _run(source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_allowed_logging_sources(tmp_path: Path) -> None:
    (tmp_path / "allowed.py").write_text(
        "import logging\n"
        "from punt_vox.logging_config import configure_client_logging\n"
        "configure_client_logging(role='cli')\n"
        "logging.getLogger(__name__).info('safe %s', 42)\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        "import logging; logging.basicConfig(level=20)",
        "from logging import basicConfig; basicConfig()",
        "from logging import basicConfig as setup; setup()",
        "from logging.handlers import RotatingFileHandler",
        "from logging.handlers import TimedRotatingFileHandler",
        "from logging.handlers import RotatingFileHandler as Handler; Handler('log')",
        "from logging.handlers import TimedRotatingFileHandler as Handler",
        "import logging.handlers as h; h.RotatingFileHandler('log')",
        "import logging.handlers as h; h.TimedRotatingFileHandler('log')",
        "from logging.handlers import RotatingFileHandler as H\nclass Sink(H): pass",
        (
            "from logging.handlers import TimedRotatingFileHandler as H\n"
            "class Sink(H): pass"
        ),
        'config = {"class": "logging.handlers.RotatingFileHandler"}',
        'config = {"class": "logging.handlers.TimedRotatingFileHandler"}',
        'config = {"class": "logging.handlers." "RotatingFileHandler"}',
    ],
)
def test_forbidden_logging_sources(tmp_path: Path, source: str) -> None:
    nested = tmp_path / "package"
    nested.mkdir()
    (nested / "bad.py").write_text(source + "\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "bad.py:" in result.stdout
    assert "forbidden logging API" in result.stdout


@pytest.mark.parametrize("source", ["def broken(", "\x00"])
def test_unparseable_source_fails_closed(tmp_path: Path, source: str) -> None:
    (tmp_path / "broken.py").write_text(source, encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "cannot read or parse" in result.stdout


def test_missing_source_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path / "missing")
    assert result.returncode == 2
    assert "directory not found" in result.stdout
