"""Classify repository opt-in without consulting daemon worker state."""

from __future__ import annotations

from typing import Self, final

from punt_vox.deposited_files import VoxMarker
from punt_vox.dirs import DEFAULT_CONFIG_DIR, find_repo_root
from punt_vox.hook_envelope import HookEnvelope


@final
class HookContextAdmission:
    """Keep hook enablement policy separate from stamping and transport."""

    __slots__ = ("_payload",)
    _payload: dict[str, object]

    def __new__(cls, payload: dict[str, object]) -> Self:
        """Bind the live hook's session identity and working directory."""
        self = super().__new__(cls)
        self._payload = payload
        return self

    def enabled(self) -> bool:
        """Classify repository opt-in without reading daemon lifecycle state."""
        cwd = HookEnvelope.cwd_of(self._payload)
        if cwd is None:
            return False
        root = find_repo_root(cwd)
        roots = (root,) if root is not None else (cwd, *cwd.parents)
        return any(
            VoxMarker(path / DEFAULT_CONFIG_DIR / "enabled", path).is_present()
            for path in roots
        )
