"""Default worker composition, separate from provider-neutral lifecycle code."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.config import ConfigStore
from punt_vox.paths import user_state_dir
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

if TYPE_CHECKING:
    from punt_vox.voxd.types import MessageHandler


@final
class WorkerSubsystem:
    """Wire the first provider and profile through replaceable name lookups."""

    __slots__ = ()

    @staticmethod
    def build(store: HookContextStore) -> dict[str, MessageHandler]:
        """Rehydrate the registry at daemon startup."""
        return WorkerHandlers.build(
            WorkerRegistry(
                WorkerRepoPolicy(ConfigStore(ConfigStore.global_dir())),
                WorkerRecords(user_state_dir() / "workers"),
                {"tmux": TmuxWorkerProvider()},
                {"claude": ClaudeProfile(store)},
            )
        )
