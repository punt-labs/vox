"""A named set of keys one background job owns on a worker's report."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.workers.model import WorkerRecord


@final
class ReportMarkers:
    """Read, strip and fold the markers of one job without touching the rest.

    A worker's report also carries late progress from the worker itself, so a
    job that finished must write back only its own keys.
    """

    __slots__ = ("_keys",)
    _keys: frozenset[str]

    def __new__(cls, *keys: str) -> Self:
        """Name the keys this job owns."""
        self = super().__new__(cls)
        self._keys = frozenset(keys)
        return self

    def present(self, record: WorkerRecord) -> bool:
        """Check whether any of the markers is on the record."""
        return bool(self._keys & record.last_report.keys())

    def strip(self, record: WorkerRecord) -> WorkerRecord:
        """Return the record with every marker removed."""
        return self.replace(record, {})

    def replace(self, record: WorkerRecord, markers: dict[str, object]) -> WorkerRecord:
        """Return the record with its markers replaced by *markers*."""
        kept = {k: v for k, v in record.last_report.items() if k not in self._keys}
        return replace(record, last_report={**kept, **markers})

    def fold(self, record: WorkerRecord, result: WorkerRecord) -> WorkerRecord:
        """Copy the markers *result* carries onto a possibly newer *record*."""
        return self.replace(
            record, {k: v for k, v in result.last_report.items() if k in self._keys}
        )
