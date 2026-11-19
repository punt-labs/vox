"""Reject private-sink bypasses in Python sources, including imports and strings."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

_FORBIDDEN = re.compile(
    r"\b(?:basicConfig|RotatingFileHandler|TimedRotatingFileHandler)\b"
)


def check(source: Path) -> int:
    """Scan identifiers and string values, failing closed on unreadable source."""
    if not source.is_dir():
        print(f"logging-sources: source directory not found: {source}")
        return 2
    count = 0
    for path in sorted(source.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            print(f"logging-sources: cannot read or parse {path}")
            return 2
        findings = {
            (getattr(node, "lineno", 1), match.group())
            for node in ast.walk(tree)
            for _, value in ast.iter_fields(node)
            if isinstance(value, str)
            for match in _FORBIDDEN.finditer(value)
        }
        for line, name in sorted(findings):
            print(f"{path}:{line}: forbidden logging API: {name}")
        count += len(findings)
    if count:
        print("logging-sources: FAIL — configure private sinks through logging_config")
        return 1
    print("logging-sources: PASS — no private-sink bypasses in source")
    return 0


def main() -> int:
    """Check the repository's src tree or an explicitly supplied fixture tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src",
    )
    return check(parser.parse_args().source)


if __name__ == "__main__":
    raise SystemExit(main())
