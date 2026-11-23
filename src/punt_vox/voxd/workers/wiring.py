"""Default worker composition, separate from provider-neutral lifecycle code."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.config import ConfigStore
from punt_vox.paths import user_state_dir
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.startup import WorkerStartup
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

if TYPE_CHECKING:
    from collections.abc import Callable

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
    def prepare(
        store: HookContextStore, policy: WorkerRepoPolicy
    ) -> tuple[WorkerActor, Callable[[], None]]:
        """Wire the actor and return the recovery it must await before serving."""
        records = WorkerRecords(user_state_dir() / "workers")
        provider = TmuxWorkerProvider()
        registry = WorkerRegistry(
            policy,
            records,
            {"tmux": provider},
            {"claude": ClaudeProfile(store)},
        )

        def recover() -> None:
            registry.restore(WorkerStartup.run(records, store, provider))

        return WorkerActor(registry), recover
