"""Clean only positively identified artifacts owned by this worker registry."""

from __future__ import annotations

import contextlib
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, final
from uuid import UUID

from punt_vox.voxd.workers.command import WorkerCommand
from punt_vox.voxd.workers.environment import WorkerEnvironment
from punt_vox.voxd.workers.startup_probes import StartupProbes

if TYPE_CHECKING:
    from punt_vox.voxd.workers.records import WorkerRecords
    from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

logger = logging.getLogger(__name__)


@final
class WorkerReaper:
    """Find orphan sessions within this registry's dedicated tmux ownership."""

    __slots__ = ()

    @classmethod
    def run(cls, records: WorkerRecords, provider: TmuxWorkerProvider) -> None:
        """Clean stale launch resources without opening any Claude config."""
        try:
            cls._sessions(records, provider)
        except (OSError, RuntimeError) as exc:
            logger.warning("Worker session reaper unavailable (%s)", type(exc).__name__)
        try:
            cls.reap_brokers(records.namespace)
        except OSError as exc:
            logger.warning("Worker broker reaper unavailable (%s)", type(exc).__name__)

    @classmethod
    def reap_brokers(cls, namespace: str) -> None:
        """Remove abandoned private brokers, never the harness configuration."""
        prefix = WorkerEnvironment.broker_prefix(namespace)
        for path in Path(tempfile.gettempdir()).glob(f"{prefix}*-????????"):
            owner = path.name.removeprefix(prefix).partition("-")[0]
            with contextlib.suppress(OSError, ValueError):
                if not cls._owner_exited(int(owner)):
                    continue
                cls._remove_broker(path)

    @staticmethod
    def _owner_exited(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    @staticmethod
    def _remove_broker(path: Path) -> None:
        info = path.lstat()
        if info.st_uid != os.getuid() or info.st_mode != stat.S_IFDIR | 0o700:
            return
        address = path / "socket"
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISSOCK(address.lstat().st_mode):
                address.unlink()
        path.rmdir()

    @classmethod
    def _sessions(cls, records: WorkerRecords, provider: TmuxWorkerProvider) -> None:
        sessions = WorkerCommand("list-sessions", "-F", "#{session_name}").run(
            check=False
        )
        StartupProbes.completed(
            sessions.stdout.splitlines(),
            lambda handle: cls._reap_session(records, provider, handle),
        )

    @classmethod
    def _reap_session(
        cls, records: WorkerRecords, provider: TmuxWorkerProvider, handle: str
    ) -> None:
        try:
            if cls._orphan(records, handle):
                provider.stop(handle)
        except (OSError, RuntimeError) as exc:
            logger.warning(
                "Worker orphan %s not reaped (%s)", handle, type(exc).__name__
            )

    @staticmethod
    def _orphan(records: WorkerRecords, handle: str) -> bool:
        prefix = "vox-worker-"
        if not handle.startswith(prefix):
            return False
        try:
            worker_id = str(UUID(handle.removeprefix(prefix)))
        except ValueError:
            return False
        if handle != prefix + worker_id or records.protects_session(worker_id):
            return False
        owner = WorkerCommand(
            "show-environment", "-t", f"={handle}", "VOX_WORKER_OWNER"
        ).run(check=False)
        return owner.returncode == 0 and owner.stdout.strip() == (
            f"VOX_WORKER_OWNER={records.namespace}:{worker_id}"
        )
