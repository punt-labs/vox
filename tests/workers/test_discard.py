"""WorkerDiscard: the pending and outcome markers of a worktree discard."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.workers.discard import WorkerDiscard
from punt_vox.voxd.workers.policy import WorkerRepoPolicy

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


def test_queued_reads_only_the_pending_marker(tmp_path: Path) -> None:
    base = _record(tmp_path)
    assert not WorkerDiscard.queued(base)
    assert WorkerDiscard.queued(
        replace(base, last_report={**base.last_report, "discard_pending": True})
    )
    assert not WorkerDiscard.queued(
        replace(base, last_report={**base.last_report, "discard_error": "busy"})
    )


def test_mark_queues_a_stopped_launch(tmp_path: Path) -> None:
    stopped = replace(_record(tmp_path), state="stopped")
    marked = WorkerDiscard.mark(stopped)
    assert marked.last_report["discard_pending"] is True
    assert "discard_skipped" not in marked.last_report


def test_mark_keeps_the_worktree_when_the_pane_may_still_be_live(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A launch whose provider could not be stopped is not terminal."""
    starting = _record(tmp_path)
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.discard"):
        kept = WorkerDiscard.mark(starting)
    assert kept.last_report["discard_skipped"] == "provider stop failed"
    assert not WorkerDiscard.queued(kept)
    [warning] = caplog.records
    assert starting.id in warning.getMessage()


@pytest.mark.parametrize(
    "unsettled", [{"discard_skipped": "provider stop failed"}, {"discard_error": "x"}]
)
def test_retry_requeues_a_skipped_or_failed_discard_once_stopped(
    tmp_path: Path, unsettled: dict[str, object]
) -> None:
    base = _record(tmp_path)
    stopped = replace(
        base, state="stopped", last_report={**base.last_report, **unsettled}
    )
    retried = WorkerDiscard.retry(stopped)
    assert retried.last_report["discard_pending"] is True
    assert not any(k in retried.last_report for k in unsettled)


def test_retry_leaves_a_record_that_owes_nothing_alone(tmp_path: Path) -> None:
    base = _record(tmp_path)
    stopped = replace(base, state="stopped")
    assert WorkerDiscard.retry(stopped) == stopped
    still_starting = replace(
        base, last_report={**base.last_report, "discard_error": "x"}
    )
    assert WorkerDiscard.retry(still_starting) == still_starting


def test_run_strips_the_pending_marker_and_records_the_policys_outcome(
    tmp_path: Path,
) -> None:
    base = _record(tmp_path)
    pending = replace(
        base, state="stopped", last_report={**base.last_report, "discard_pending": True}
    )
    policy = Mock(spec=WorkerRepoPolicy)
    policy.discard_recorded.side_effect = _discarded
    done = WorkerDiscard.run(pending, policy)
    handed = policy.discard_recorded.call_args.args[0]
    assert "discard_pending" not in handed.last_report
    assert done.last_report["worktree_discarded"] is True
    assert not WorkerDiscard.queued(done)


def test_run_records_an_unexpected_policy_error_with_its_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base = _record(tmp_path)
    pending = replace(
        base, state="stopped", last_report={**base.last_report, "discard_pending": True}
    )
    policy = Mock(spec=WorkerRepoPolicy)
    policy.discard_recorded.side_effect = RuntimeError("policy exploded")
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.discard"):
        failed = WorkerDiscard.run(pending, policy)
    assert failed.last_report["discard_error"] == "RuntimeError: policy exploded"
    assert not WorkerDiscard.queued(failed)
    [warning] = caplog.records
    assert base.id in warning.getMessage()
    assert warning.exc_info is not None
