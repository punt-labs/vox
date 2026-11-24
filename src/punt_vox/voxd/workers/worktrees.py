"""Create a worker's git worktree, and discard one whose launch never started."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, final
from uuid import UUID

if TYPE_CHECKING:
    from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)

_WORKTREE_LEAF: Final = "worktree"


@final
class WorkerWorktrees:
    """Own the git commands that make and unmake a worker's checkout."""

    # Seconds: `git worktree add` on a loaded machine has taken over 20.
    CREATE_DEADLINE: ClassVar[int] = 90
    __slots__ = ()

    def create(self, project: Path, worktree: Path, branch: str) -> None:
        """Keep the source checkout untouched and retain the new branch."""
        started = time.monotonic()
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project),
                    "worktree",
                    "add",
                    "-b",
                    branch,
                    str(worktree),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.CREATE_DEADLINE,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                "worker start timed out creating the worktree after "
                f"{self.CREATE_DEADLINE} s"
            ) from exc
        logger.info(
            "Worker worktree for %s created in %.1f s",
            project.name,
            time.monotonic() - started,
        )

    def discard_recorded(self, record: WorkerRecord) -> WorkerRecord:
        """Discard a failed launch's worktree and note the outcome on its record.

        A launch whose provider could not be stopped is not terminal: its pane
        may still be live, so the checkout is kept and ``discard_skipped``
        says why. A discard that fails is logged by exception type and
        recorded as ``discard_error``; the rollback itself still completes,
        since the record's failure is the fact that matters to the caller.
        """
        if record.state != "stopped":
            logger.warning("Worker %s worktree kept: provider stop failed", record.id)
            return replace(
                record,
                last_report={
                    **record.last_report,
                    "discard_skipped": "provider stop failed",
                },
            )
        note: dict[str, object] = {"worktree_discarded": True}
        try:
            self.discard(Path(record.project), Path(record.worktree), record.id)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning("Worker worktree discard failed (%s)", type(exc).__name__)
            note = {"discard_error": type(exc).__name__}
        return replace(record, last_report={**record.last_report, **note})

    def discard(self, project: Path, worktree: Path, worker_id: str) -> None:
        """Remove only this worker's own worktree and its vox-worker branch.

        A launch that failed left nothing of value, unlike a stop. The path
        must be the inventory's ``<worker-id>/worktree`` and the branch is
        derived from the id, so no other checkout or branch can be removed.
        ``git worktree remove --force`` unregisters this worker's entry whether
        or not its directory still exists; the repository is never pruned,
        since that would drop another checkout whose directory is away for a
        moment.
        """
        owner = str(UUID(worker_id))
        if worktree.name != _WORKTREE_LEAF or worktree.parent.name != owner:
            raise ValueError(f"not the worker's own worktree: {worktree}")
        if self._registered(project, worktree):
            self._git(project, "worktree", "remove", "--force", str(worktree))
        if worktree.exists():
            shutil.rmtree(worktree)
        branch = f"vox-worker/{owner}"
        if self._has_branch(project, branch):
            self._git(project, "branch", "-D", branch)
        if worktree.parent.is_dir() and not any(worktree.parent.iterdir()):
            worktree.parent.rmdir()

    def _registered(self, project: Path, worktree: Path) -> bool:
        listing = self._git(project, "worktree", "list", "--porcelain")
        return f"worktree {worktree.resolve()}\n" in listing or (
            f"worktree {worktree}\n" in listing
        )

    @staticmethod
    def _has_branch(project: Path, branch: str) -> bool:
        """Probe the branch: exit 1 is absent; any other failure is an error.

        Reading a broken probe as absent would leave the branch behind while
        the record still said the discard succeeded.
        """
        probe = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if probe.returncode not in (0, 1):
            raise subprocess.CalledProcessError(
                probe.returncode, probe.args, probe.stdout, probe.stderr
            )
        return probe.returncode == 0

    @staticmethod
    def _git(project: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(project), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
