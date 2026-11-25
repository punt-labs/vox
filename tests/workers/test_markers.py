"""ReportMarkers: one job's keys on a worker's report, never the rest."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.workers.markers import ReportMarkers

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.workers.model import WorkerRecord


def _record(tmp_path: Path) -> WorkerRecord:
    registry = _registry(tmp_path, FakeWorkerProvider())
    return registry.start("work", str(tmp_path), provider="fake", harness="test")


def test_present_sees_any_owned_key_and_nothing_else(tmp_path: Path) -> None:
    markers = ReportMarkers("a_pending", "a_error")
    bare = _record(tmp_path)
    assert not markers.present(bare)
    assert markers.present(
        replace(bare, last_report={**bare.last_report, "a_error": "busy"})
    )
    assert not markers.present(
        replace(bare, last_report={**bare.last_report, "b_pending": True})
    )


def test_replace_swaps_only_the_owned_keys(tmp_path: Path) -> None:
    """Keys outside the set, including the worker's own progress, survive."""
    markers = ReportMarkers("a_pending", "a_error")
    before = replace(
        _record(tmp_path),
        last_report={"a_pending": True, "a_error": "old", "worker_seq": 3},
    )
    after = markers.replace(before, {"a_error": "new"})
    assert after.last_report == {"a_error": "new", "worker_seq": 3}


def test_strip_removes_every_owned_key(tmp_path: Path) -> None:
    markers = ReportMarkers("a_pending", "a_error")
    before = replace(
        _record(tmp_path), last_report={"a_pending": True, "worker_seq": 3}
    )
    assert markers.strip(before).last_report == {"worker_seq": 3}


def test_fold_copies_owned_keys_from_the_result_onto_a_newer_record(
    tmp_path: Path,
) -> None:
    """The newer record's other keys win; only the job's keys come across."""
    markers = ReportMarkers("a_pending", "a_error")
    base = _record(tmp_path)
    result = replace(base, last_report={"a_error": "busy", "worker_seq": 1})
    newer = replace(base, last_report={"a_pending": True, "worker_seq": 9})
    folded = markers.fold(newer, result)
    assert folded.last_report == {"a_error": "busy", "worker_seq": 9}
