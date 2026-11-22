"""Worker JSON conversion is confined to disk and RPC boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import TypedDict, cast, final

from punt_vox.voxd.workers.model import WorkerRecord, WorkerState

__all__ = ["WorkerPayload", "WorkerRecordCodec"]


class WorkerPayload(TypedDict):
    """Disk/wire memento, never a live mutable worker entity."""

    id: str
    provider: str
    harness: str
    session: str
    project: str
    worktree: str
    branch: str
    task: str
    state: WorkerState
    created_at: str
    last_report: dict[str, object]  # PY-TS-14: extensible harness JSON at the edge.


@final
class WorkerRecordCodec:
    """Narrow untrusted JSON into an immutable, typed domain checkpoint."""

    __slots__ = ()

    @staticmethod
    def encode(record: WorkerRecord) -> WorkerPayload:
        """Expose JSON-compatible data only when crossing a serialization edge."""
        return {
            "id": record.id,
            "provider": record.provider,
            "harness": record.harness,
            "session": record.session,
            "project": record.project,
            "worktree": record.worktree,
            "branch": record.branch,
            "task": record.task,
            "state": record.state,
            "created_at": record.created_at,
            "last_report": deepcopy(dict(record.last_report)),
        }

    @staticmethod
    def decode(data: object) -> WorkerRecord:
        """Validate all common fields before admitting a durable worker record."""
        if not isinstance(data, dict):
            raise ValueError("Invalid worker record")
        fields = cast("dict[str, object]", data)
        if (
            not all(
                isinstance(fields.get(key), str)
                for key in WorkerPayload.__required_keys__ - {"last_report"}
            )
            or fields.get("state") not in ("starting", "ready", "exited", "stopped")
            or not isinstance(fields.get("last_report"), dict)
        ):
            raise ValueError("Invalid worker record")
        payload = {key: fields[key] for key in WorkerPayload.__required_keys__}
        return WorkerRecord(**cast("WorkerPayload", payload))
