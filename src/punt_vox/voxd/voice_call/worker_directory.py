"""Resolve spoken worker identities against the provider-neutral live roster."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.contracts import WorkerControl
    from punt_vox.voxd.workers.types import WorkerPayload


@final
class WorkerDirectory:
    """Own exact-ID and unambiguous spoken-prefix lookup."""

    __slots__ = ("_workers",)
    _workers: WorkerControl

    def __new__(cls, workers: WorkerControl) -> Self:
        """Bind the live worker roster without retaining stale payloads."""
        self = super().__new__(cls)
        self._workers = workers
        return self

    async def find(self, wanted: str) -> WorkerPayload:
        """Resolve an exact ID or a unique prefix of at least four characters."""
        workers = await self._workers.workers()
        exact = [w for w in workers if w["id"] == wanted]
        prefixed = [
            w for w in workers if len(wanted) >= 4 and w["id"].startswith(wanted)
        ]
        matches = exact or prefixed
        if len(matches) != 1:
            raise ValueError(f"no single worker matches {wanted!r}")
        return matches[0]
