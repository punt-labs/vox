"""Tests for punt_vox.service.kill_ledger -- what stop did, recorded as it acted."""

from __future__ import annotations

from _lifecycle_fakes import FakeHandle, FakeWorld

from punt_vox.service.kill_ledger import KillLedger, KillOutcome
from punt_vox.service.termination import Termination


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
    ledger.settle(gone, Termination.EXITED)
    ledger.settle(full, Termination.SURVIVED)
    ledger.settle(term_only, Termination.TERM_ONLY)
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
        "stop began signalling voxd pid 200; its exit was never confirmed"
    ]


def test_confirm_moves_only_signalled_handles_to_gone() -> None:
    world = FakeWorld()
    ledger = KillLedger()
    late, spared, stranger = (_handle(world, p) for p in (200, 300, 400))
    ledger.signalling(late)
    ledger.settle(late, Termination.TERM_ONLY)
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


def test_a_refused_signal_is_its_own_outcome() -> None:
    """Permission denied reads as refused, not "got SIGTERM only"."""
    world = FakeWorld()
    ledger = KillLedger()
    refused = _handle(world, 200)
    ledger.signalling(refused)
    ledger.settle(refused, Termination.REFUSED)
    assert ledger.account() == ["voxd pid 200 refused the signal: permission denied"]


def test_a_refused_process_stays_watched_and_is_never_called_killed() -> None:
    """A refusal is watched until it exits; its exit is its own, not a kill."""
    world = FakeWorld()
    ledger = KillLedger()
    refused = _handle(world, 200)
    ledger.signalling(refused)
    ledger.settle(refused, Termination.REFUSED)
    assert ledger.unconfirmed() == frozenset({refused})
    ledger.confirm([refused])
    assert ledger.gone(refused)
    assert ledger.account() == [
        "voxd pid 200 refused the signal, then exited on its own"
    ]


def test_survived_wording_says_both_signals_were_delivered() -> None:
    """Permission denied is REFUSED now, so SURVIVED does not mention it."""
    line = KillOutcome.SURVIVED.line(7)
    assert line == "voxd pid 7 did not exit after SIGTERM and SIGKILL were delivered"


def test_a_refused_sigkill_after_sigterm_says_so() -> None:
    """SIGTERM went out, then SIGKILL was refused."""
    world = FakeWorld()
    ledger = KillLedger()
    handle = _handle(world, 200)
    ledger.signalling(handle)
    ledger.settle(handle, Termination.KILL_REFUSED)
    assert handle in ledger.unconfirmed()
    assert ledger.account() == [
        "voxd pid 200 got SIGTERM, but SIGKILL was refused: permission denied"
    ]
