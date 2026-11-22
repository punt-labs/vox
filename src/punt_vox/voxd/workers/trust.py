"""Add and remove only the exact worker worktree's Claude trust entry."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Self, cast, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.sibling_lock import SiblingLock
from punt_vox.voxd.workers.claude_lease import ClaudeConfigLease


@final
class ClaudeTrust:
    """Preserve the user's settings while accepting a worker worktree."""

    __slots__ = ("_path",)
    _path: Path

    def __new__(cls) -> Self:
        """Locate the user's normal inherited configuration."""
        self = super().__new__(cls)
        directory = os.environ.get("CLAUDE_CONFIG_DIR")
        self._path = (
            Path(directory).resolve() / ".claude.json"
            if directory
            else Path.home() / ".claude.json"
        )
        return self

    def update(self, worktree: Path, *, accepted: bool) -> None:
        """Atomically change trust, retaining unrelated config and project keys."""
        with SiblingLock(self._path).held(), ClaudeConfigLease(self._path).held():
            self._replace(worktree, accepted=accepted)

    def _replace(self, worktree: Path, *, accepted: bool) -> None:
        """Keep the JSON edge separate from acquisition and lease ownership."""
        target = AtomicFile(self._path)
        data = json.loads(target.read() or "{}")
        if not isinstance(data, dict):
            raise ValueError("Claude configuration must be a JSON object")
        root = cast("dict[str, object]", data)
        projects = root.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise ValueError("Claude projects configuration must be an object")
        entries = cast("dict[str, object]", projects)
        key = str(worktree.resolve())
        project = entries.setdefault(key, {})
        if not isinstance(project, dict):
            raise ValueError("Claude project configuration must be an object")
        settings = cast("dict[str, object]", project)
        if accepted:
            settings["hasTrustDialogAccepted"] = True
        else:
            settings.pop("hasTrustDialogAccepted", None)
            if not settings:
                entries.pop(key, None)
        target.replace(json.dumps(root, indent=2) + "\n", mode=0o600)
