"""Every relative cross-reference in a conversation-mode docstring names a real object.

A reference such as ``:class:`~.call_agent_errors.CallAgentSpawnError``` sends
a reader to a module; when the object moves and the reference does not, the
reader is sent to the wrong place.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

import punt_vox.voxd.conversation_mode as package

_REFERENCE = re.compile(r":(?:class|data|meth|func|attr|exc):`~?\.([\w.]+)`")

_MODULES = sorted(
    info.name for info in pkgutil.iter_modules(package.__path__) if not info.ispkg
)

_MISSING = object()


def _resolves(target: str) -> bool:
    module_name, *attributes = target.split(".")
    try:
        found: object = importlib.import_module(f"{package.__name__}.{module_name}")
    except ModuleNotFoundError:
        return False
    for attribute in attributes:
        found = getattr(found, attribute, _MISSING)
    return found is not _MISSING


@pytest.mark.parametrize("module", _MODULES)
def test_every_relative_reference_resolves(module: str) -> None:
    source = Path(package.__path__[0], f"{module}.py")
    targets = _REFERENCE.findall(source.read_text(encoding="utf-8"))
    assert [target for target in targets if not _resolves(target)] == []
