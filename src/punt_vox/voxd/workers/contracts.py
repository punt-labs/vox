"""Worker provider and harness contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final

from punt_vox.voxd.workers.steering_types import DecisionChoice

__all__ = ["HarnessProfile", "WorkerLaunch", "WorkerProvider"]


@final
@dataclass(frozen=True, slots=True)
class WorkerLaunch:
    """Provider-neutral launch identity and work product location."""

    handle: str
    worker_id: str
    worktree: Path
    task: str


@dataclass(frozen=True, slots=True)
class WorkerInput:
    """An observed input receipt, never an idle/turn state (Stop is not required)."""

    seq: int
    text: str
    decision: bool = False


class HarnessProfile(Protocol):
    """Describe a harness without coupling its launcher to its implementation."""

    @property
    def submit_key(self) -> str:
        """Return the key which submits input."""
        ...

    @property
    def interrupt_key(self) -> str:
        """Return the key which interrupts current work."""
        ...

    @property
    def settle_delay(self) -> float:
        """Seconds needed after interrupt and literal input before submission."""
        ...

    @property
    def input_limit(self) -> int:
        """Return the largest input whose receipt text remains untruncated."""
        ...

    # PY-TS-14: absence means this worker has no observed input yet.
    def last_input(self, worker_id: str) -> WorkerInput | None:
        """Return the latest observed input, if any."""
        ...

    def matches_input(self, receipt: WorkerInput, words: str) -> bool:
        """Compare input using the harness's bounded representation."""
        ...

    def decision_keys(self, choice: DecisionChoice) -> tuple[str, ...]:
        """Select a permission choice explicitly, never submit arbitrary text."""
        ...

    def inputs_since(self, worker_id: str, baseline: int) -> list[WorkerInput]:
        """Return bounded recent inputs strictly newer than the baseline."""
        ...

    def argv(self, worker_id: str, task: str) -> list[str]:
        """Build the harness launch arguments."""
        ...

    def environment(self, parent: Mapping[str, str]) -> dict[str, str]:
        """Select only explicitly admitted environment variables."""
        ...

    def prepare(self, worktree: Path) -> None:
        """Prepare the harness, or do nothing when unnecessary."""
        ...

    def cleanup(self, worktree: Path) -> None:
        """Undo preparation without deleting work product."""
        ...

    # PY-TS-14: profiles supply extensible JSON snapshots, not shared domain state.
    def progress(self, worker_id: str) -> dict[str, object]:
        """Read the harness progress source."""
        ...


class WorkerProvider(Protocol):
    """Launch and control workers; handles are durable provider-owned names."""

    def start(
        self,
        launch: WorkerLaunch,
        profile: HarnessProfile,
    ) -> None:
        """Launch a persistent worker."""
        ...

    def steer(
        self, handle: str, words: str, *, urgent: bool, profile: HarnessProfile
    ) -> None:
        """Submit words, optionally interrupting first."""
        ...

    def status(self, handle: str) -> bool:
        """Return whether the worker process is alive."""
        ...

    def decide(
        self, handle: str, choice: DecisionChoice, *, profile: HarnessProfile
    ) -> None:
        """Send only the harness's explicit permission-choice keys."""
        ...

    def stop(self, handle: str) -> None:
        """Terminate the worker and its process group."""
        ...

    # PY-TS-14: provider-specific JSON snapshots share only session_started.
    def progress(self, worker_id: str, profile: HarnessProfile) -> dict[str, object]:
        """Read current progress independently of process liveness."""
        ...
