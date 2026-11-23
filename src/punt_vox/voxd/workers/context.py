"""Compose workers, hook ingestion and Mode B calls around one context store."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final

from punt_vox.voxd.hook_context_handlers import HookContextHandlers
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.voice_call.handlers import CallHandlers
from punt_vox.voxd.voice_call.progress import ProgressEvents
from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.wiring import WorkerSubsystem

if TYPE_CHECKING:
    from contextlib import ExitStack

    from starlette.websockets import WebSocket

    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.types import MessageHandler

logger = logging.getLogger(__name__)


@final
class WorkerContext:
    """Keep the shared hook store's identity inside the composition boundary."""

    __slots__ = ()

    @staticmethod
    def handlers(
        playback: PlaybackQueue,
        lifetime: ExitStack
        | None = None,  # PY-TS-14: standalone handlers lack a lifespan.
    ) -> dict[str, MessageHandler]:
        """Wire ingestion, workers and calls with one store and one worker actor."""
        store = HookContextStore()
        try:
            policy = WorkerSubsystem.policy()
            actor = WorkerSubsystem.recover(store, policy)
        except Exception:
            logger.exception("Worker startup recovery failed")
            return WorkerContext._degraded(store)
        if lifetime is not None:
            lifetime.callback(actor.shutdown)
        events = ProgressEvents()
        return {
            **WorkerHandlers(actor).handlers(store, events),
            **CallHandlers(actor, policy, playback, progress=events).handlers(),
        }

    @staticmethod
    def _degraded(store: HookContextStore) -> dict[str, MessageHandler]:
        unavailable = dict.fromkeys(
            (*WorkerHandlers.ROUTES, *CallHandlers.ROUTES, "worker_hook_admission"),
            WorkerContext._unavailable,
        )
        return {**HookContextHandlers(store).handlers(), **unavailable}

    @staticmethod
    async def _unavailable(msg: dict[str, object], websocket: WebSocket) -> None:
        await WireReply(websocket, str(msg.get("id", ""))).error(
            "Worker startup recovery failed; check the daemon log"
        )
