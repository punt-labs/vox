"""voxd package -- audio server daemon."""
# pyright: reportPrivateUsage=false
# Re-exporting private names from submodules is the whole point of __init__.py.

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from punt_vox.voxd.daemon import build_app, entrypoint
    from punt_vox.voxd.health import DaemonHealth
    from punt_vox.voxd.playback import _PLAYBACK_TIMEOUT_DEFAULT_S, PlaybackQueue

__all__ = [
    "_PLAYBACK_TIMEOUT_DEFAULT_S",
    "DaemonHealth",
    "PlaybackQueue",
    "build_app",
    "entrypoint",
]

# Lazily resolve the daemon re-exports (PEP 562). Importing a light submodule
# such as the wire value types under ``voxd.programs`` therefore no longer
# drags in the whole daemon -- Starlette, pydub, and the provider SDKs -- so a
# thin client can name those types without paying the daemon's import cost.
_LAZY = {
    "build_app": "punt_vox.voxd.daemon",
    "entrypoint": "punt_vox.voxd.daemon",
    "DaemonHealth": "punt_vox.voxd.health",
    "PlaybackQueue": "punt_vox.voxd.playback",
    "_PLAYBACK_TIMEOUT_DEFAULT_S": "punt_vox.voxd.playback",
}


def __getattr__(name: str) -> object:
    """Import a daemon re-export on first access, or raise ``AttributeError``."""
    module = _LAZY.get(name)
    if module is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return getattr(importlib.import_module(module), name)
