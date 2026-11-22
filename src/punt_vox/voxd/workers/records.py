"""Private atomic JSON records survive daemon restarts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self, cast, final
from uuid import UUID

from punt_vox.atomic_file import AtomicFile


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

    def save(self, record: dict[str, object]) -> None:
        """Commit a full record with owner-only permissions."""
        worker_id = str(UUID(str(record["id"])))
        AtomicFile(self._root / f"{worker_id}.json").replace(
            json.dumps(record, indent=2) + "\n", mode=0o600
        )

    def load(self) -> list[dict[str, object]]:
        """Read durable records; malformed records fail loudly, never vanish."""
        records: list[dict[str, object]] = []
        for path in sorted(self._root.glob("*.json")):
            record = json.loads(path.read_text())
            if not isinstance(record, dict):
                raise ValueError(f"Invalid worker record: {path}")
            fields = cast("dict[str, object]", record)
            if str(UUID(str(fields.get("id")))) != path.stem:
                raise ValueError(f"Invalid worker record: {path}")
            records.append(fields)
        return records
