"""Compose worker readiness and hook ingestion around one context store."""

from __future__ import annotations

from typing import final

from punt_vox.voxd.hook_context_handlers import HookContextHandlers
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.types import MessageHandler
from punt_vox.voxd.workers.wiring import WorkerSubsystem


@final
class WorkerContext:
    """Keep the shared hook store's identity inside the composition boundary."""

    __slots__ = ()

    @staticmethod
    def handlers() -> dict[str, MessageHandler]:
        """Wire ingestion and workers with the same daemon-local hook store."""
        store = HookContextStore()
        return {
            **HookContextHandlers(store).handlers(),
            **WorkerSubsystem.build(store).handlers(),
        }
