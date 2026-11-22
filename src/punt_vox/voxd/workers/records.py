"""Private atomic JSON records survive daemon restarts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self, cast, final
from uuid import UUID

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.workers.model import WorkerRecord


@final
class WorkerRecords:
    """Own the private worker state directory and validate record identities."""

    __slots__ = ("_root",)
    _root: Path

    def __new__(cls, root: Path) -> Self:
        """Create a private persistent record directory."""
        self = super().__new__(cls)
        self._root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        return self

    def worktree(self, worker_id: str) -> Path:
        """Return a worker's dedicated work product directory."""
        return self._root / str(UUID(worker_id)) / "worktree"

    def save(self, record: WorkerRecord) -> None:
        """Commit a full record with owner-only permissions."""
        worker_id = str(UUID(record["id"]))
        AtomicFile(self._root / f"{worker_id}.json").replace(
            json.dumps(record, indent=2) + "\n", mode=0o600
        )

    def owns_session(self, session_id: object, cwd: Path) -> bool:
        """Admit hooks only for a live registered session in its exact worktree."""
        if not isinstance(session_id, str):
            return False
        try:
            worker_id = str(UUID(session_id))
            record = json.loads((self._root / f"{worker_id}.json").read_text())
            if not isinstance(record, dict):
                return False
            fields = cast("dict[str, object]", record)
            return (
                fields.get("id") == worker_id
                and fields.get("state") in ("starting", "ready")
                and fields.get("worktree") == str(cwd.resolve())
            )
        except (OSError, ValueError):
            return False

    def load(self) -> list[WorkerRecord]:
        """Read durable records; malformed records fail loudly, never vanish."""
        records: list[WorkerRecord] = []
        for path in sorted(self._root.glob("*.json")):
            record = json.loads(path.read_text())
            if not isinstance(record, dict):
                raise ValueError(f"Invalid worker record: {path}")
            fields = cast("dict[str, object]", record)
            if (
                not all(
                    isinstance(fields.get(k), str)
                    for k in WorkerRecord.__required_keys__ - {"last_report"}
                )
                or fields.get("state") not in ("starting", "ready", "exited", "stopped")
                or not isinstance(fields.get("last_report"), dict)
            ):
                raise ValueError(f"Invalid worker record: {path}")
            if str(UUID(str(fields.get("id")))) != path.stem:
                raise ValueError(f"Invalid worker record: {path}")
            records.append(cast("WorkerRecord", fields))
        return records
