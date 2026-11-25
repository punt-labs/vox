"""WorkerSettlement and SettledDebts: run the owed debts, fold only their markers."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.workers.cleanup import WorkerCleanup
from punt_vox.voxd.workers.discard import WorkerDiscard
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.settlement import SettledDebts, WorkerSettlement

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.workers.model import WorkerRecord


def _discarded(record: WorkerRecord) -> WorkerRecord:
    """A policy double's outcome: the discard ran and succeeded."""
    return replace(
        record, last_report={**record.last_report, "worktree_discarded": True}
    )


def _record(tmp_path: Path) -> WorkerRecord:
    registry = _registry(tmp_path, FakeWorkerProvider())
    return registry.start("work", str(tmp_path), provider="fake", harness="test")


def _settlement(policy: Mock | None = None) -> WorkerSettlement:
    return WorkerSettlement(
        {"test": TrivialProfile()}, policy or Mock(spec=WorkerRepoPolicy)
    )


def test_owed_is_true_for_either_debt(tmp_path: Path) -> None:
    base = _record(tmp_path)
    assert not WorkerSettlement.owed(base)
    assert WorkerSettlement.owed(base.retire())
    assert WorkerSettlement.owed(
        replace(base, last_report={**base.last_report, "discard_pending": True})
    )


def test_prompt_is_false_only_while_a_discard_is_queued(tmp_path: Path) -> None:
    """Harness cleanup settles within a reply; a queued discard may not."""
    base = _record(tmp_path)
    assert WorkerSettlement.prompt(base.retire())
    assert not WorkerSettlement.prompt(
        replace(base, last_report={**base.last_report, "discard_pending": True})
    )


def test_settle_runs_only_the_owed_debts_and_names_their_markers(
    tmp_path: Path,
) -> None:
    retired = _record(tmp_path).retire()
    settled = _settlement().settle(retired)
    assert settled.result.last_report.get("cleanup_pending") is None
    assert not WorkerCleanup.pending(settled.result)
    assert settled.fold(retired).last_report == settled.result.last_report


def test_settle_runs_the_discard_when_queued(tmp_path: Path) -> None:
    base = _record(tmp_path)
    pending = replace(
        base, state="stopped", last_report={**base.last_report, "discard_pending": True}
    )
    policy = Mock(spec=WorkerRepoPolicy)
    policy.discard_recorded.side_effect = _discarded
    settled = _settlement(policy).settle(pending)
    assert settled.result.last_report["worktree_discarded"] is True
    assert not WorkerDiscard.queued(settled.result)


def test_fold_writes_back_only_the_markers_of_the_debts_that_ran(
    tmp_path: Path,
) -> None:
    """A debt marked by a concurrent stop survives a job that did not run it."""
    base = _record(tmp_path)
    cleaned = replace(base, last_report={**base.last_report, "worker_seq": 1})
    settled = SettledDebts(cleaned, (WorkerCleanup.MARKERS,))
    newer = replace(
        base,
        last_report={
            **base.last_report,
            "cleanup_pending": True,
            "discard_pending": True,
            "worker_seq": 9,
        },
    )
    folded = settled.fold(newer)
    assert "cleanup_pending" not in folded.last_report
    assert folded.last_report["discard_pending"] is True
    assert folded.last_report["worker_seq"] == 9


def test_fold_with_no_markers_leaves_the_newer_record_untouched(
    tmp_path: Path,
) -> None:
    base = _record(tmp_path)
    settled = SettledDebts(base, ())
    newer = replace(base, last_report={**base.last_report, "worker_seq": 2})
    assert settled.fold(newer) == newer


def test_a_failed_cleanup_is_still_folded_as_that_debts_outcome(
    tmp_path: Path,
) -> None:
    retired = _record(tmp_path).retire()
    with patch.object(TrivialProfile, "cleanup", side_effect=OSError("busy")):
        settled = _settlement().settle(retired)
    assert settled.result.last_report["cleanup_error"] == "OSError"
    assert settled.fold(retired).last_report["cleanup_error"] == "OSError"
