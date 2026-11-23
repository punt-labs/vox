"""Default worker composition, separate from provider-neutral lifecycle code."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.config import ConfigStore
from punt_vox.paths import user_state_dir
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.recovery import WorkerRecovery
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

if TYPE_CHECKING:
    from punt_vox.voxd.hook_context_store import HookContextStore


@final
class WorkerSubsystem:
    """Wire the first provider and profile through replaceable name lookups."""

    __slots__ = ()

    @staticmethod
    def policy() -> WorkerRepoPolicy:
        """Return the repository allowlist read from the global vox config."""
        return WorkerRepoPolicy(ConfigStore(ConfigStore.global_dir()))

    @staticmethod
    def actor(store: HookContextStore, policy: WorkerRepoPolicy) -> WorkerActor:
        """Rehydrate the registry at daemon startup behind one serialized actor."""
        records = WorkerRecords(user_state_dir() / "workers")
        provider = TmuxWorkerProvider()
        WorkerRecovery.restore(records, store)
        WorkerReaper.run(records, provider)
        return WorkerActor(
            WorkerRegistry(
                policy,
                records,
                {"tmux": provider},
                {"claude": ClaudeProfile(store)},
            )
        )
