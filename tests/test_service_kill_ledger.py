"""Tests for punt_vox.service.kill_ledger -- what stop did, recorded as it acted."""

from __future__ import annotations

from _lifecycle_fakes import FakeHandle, FakeWorld

from punt_vox.service.kill_ledger import KillLedger, KillOutcome


def _handle(world: FakeWorld, pid: int) -> FakeHandle:
    return FakeHandle(world.spawn(pid))


def test_a_judged_handle_is_never_judged_again() -> None:
    world = FakeWorld()
    ledger = KillLedger()
    spared = _handle(world, 300)
    ledger.spare(spared)
    assert ledger.judged(spared)
    assert ledger.judged(FakeHandle(world.procs[300]))  # value equality
    assert not ledger.judged(_handle(world, 200))


def test_settle_records_the_signal_that_was_possible() -> None:
    world = FakeWorld()
    ledger = KillLedger()
    gone, full, term_only = (_handle(world, p) for p in (200, 201, 202))
    for handle in (gone, full, term_only):
        ledger.signalling(handle)
    ledger.settle(gone, exited=True, full=True)
    ledger.settle(full, exited=False, full=True)
    ledger.settle(term_only, exited=False, full=False)
    assert ledger.gone(gone)
    assert ledger.unconfirmed() == frozenset({full, term_only})
    assert ledger.account() == [
        "killed unmanaged voxd pid 200",
        KillOutcome.SURVIVED.line(201),
        KillOutcome.TERM_ONLY.line(202),
    ]


def test_a_signal_whose_terminate_failed_is_still_named() -> None:
    world = FakeWorld()
    ledger = KillLedger()
    ledger.signalling(_handle(world, 200))
    assert ledger.account() == [
        "voxd pid 200 was signalled, but its exit was never confirmed"
    ]


def test_confirm_moves_only_signalled_handles_to_gone() -> None:
    world = FakeWorld()
    ledger = KillLedger()
    late, spared, stranger = (_handle(world, p) for p in (200, 300, 400))
    ledger.signalling(late)
    ledger.settle(late, exited=False, full=False)
    ledger.spare(spared)
    assert not ledger.any_gone()
    ledger.confirm([late, spared, stranger])
    assert ledger.gone(late)
    assert not ledger.gone(spared)
    assert not ledger.judged(stranger)
    assert ledger.any_gone()
    assert ledger.account() == [
        "killed unmanaged voxd pid 200",
        "voxd pid 300 spared: voxd.service still owns it",
    ]


def test_term_only_reason_names_the_deadline_not_ignored_signals() -> None:
    line = KillOutcome.TERM_ONLY.line(7)
    assert "no time for a grace period" in line
    assert "ignored" not in line
