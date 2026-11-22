"""Compose workers, hook ingestion and Mode B calls around one context store."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.voice_call.handlers import CallHandlers
from punt_vox.voxd.workers.wiring import WorkerSubsystem

if TYPE_CHECKING:
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.types import MessageHandler


@final
class WorkerContext:
    """Keep the shared hook store's identity inside the composition boundary."""

    __slots__ = ()

    @staticmethod
    def handlers(playback: PlaybackQueue) -> dict[str, MessageHandler]:
        """Wire ingestion, workers and calls with one store and one worker actor."""
        store = HookContextStore()
        workers = WorkerSubsystem.actor(store)
        return {
            **workers.handlers(store),
            **CallHandlers(workers, playback).handlers(),
        }
