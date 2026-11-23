"""Resolve spoken worker identities against the provider-neutral live roster."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.contracts import WorkerControl
    from punt_vox.voxd.workers.types import WorkerPayload


@final
class WorkerDirectory:
    """Own exact-ID, spoken alias, and unambiguous prefix lookup."""

    __slots__ = ("_labels", "_workers")
    _workers: WorkerControl
    _labels: dict[str, str]

    def __new__(
        cls, workers: WorkerControl, spoken_labels: dict[str, str] | None = None
    ) -> Self:
        """Bind the live worker roster without retaining stale payloads."""
        self = super().__new__(cls)
        self._workers = workers
        self._labels = {} if spoken_labels is None else spoken_labels
        return self

    async def find(self, wanted: str) -> WorkerPayload:
        """Resolve an ID, spoken alias, or unique prefix of at least four chars."""
        wanted = self._labels.get(wanted.strip().casefold(), wanted)
        workers = await self._workers.workers()
        exact = [w for w in workers if w["id"] == wanted]
        prefixed = [
            w for w in workers if len(wanted) >= 4 and w["id"].startswith(wanted)
        ]
        matches = exact or prefixed
        if len(matches) != 1:
            raise ValueError(f"no single worker matches {wanted!r}")
        return matches[0]
