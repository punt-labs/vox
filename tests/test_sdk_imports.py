"""Each provider SDK is imported by exactly one source module.

One importer per SDK keeps the SDK's types and errors from leaking into the
daemon: everything else sees vox types, so an SDK upgrade touches one file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "punt_vox"

# The SDK top-level package, and the one module allowed to import it.
_OWNER: dict[str, str] = {
    "elevenlabs": "providers/elevenlabs_client.py",
    "openai": "providers/openai.py",
}


def _imported_packages(path: Path) -> set[str]:
    """Return the top-level packages *path* imports absolutely, guarded or not."""
    packages: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            packages.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            packages.add(node.module.partition(".")[0])
    return packages


@pytest.mark.parametrize("sdk", sorted(_OWNER))
def test_only_the_owner_imports_the_sdk(sdk: str) -> None:
    importers = {
        path.relative_to(_SRC).as_posix()
        for path in _SRC.rglob("*.py")
        if sdk in _imported_packages(path)
    }

    assert importers == {_OWNER[sdk]}
