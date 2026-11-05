"""The kill ledger's invariants, asserted by name over every transition sequence.

The ledger's transitions are finite: a process is spared, or signalled and then
either never settled (``terminate()`` raised) or settled with one of the
``Termination`` outcomes; after that, ``confirm()`` may report it gone any
number of times. This suite enumerates every such sequence (with zero, one and
two confirms), replays it on a fresh ledger, and checks each
:class:`~punt_vox.service.kill_ledger.LedgerInvariant` after every step against
an oracle derived from the sequence alone -- not from the ledger's own tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from _lifecycle_fakes import FakeHandle, FakeWorld

from punt_vox.service.kill_ledger import KillLedger, KillOutcome, LedgerInvariant
from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from collections.abc import Callable

_PID = 200

# The report lines that say the process may still be running. Spelled out here
# rather than read from KillOutcome.unconfirmed, so the oracle does not move
# when the code under test does.
_ALIVE = (
    KillOutcome.SIGNALLING,
    KillOutcome.SURVIVED,
    KillOutcome.TERM_ONLY,
    KillOutcome.REFUSED,
    KillOutcome.KILL_REFUSED,
)


@dataclass(frozen=True, slots=True)
class Sequence:
    """One path through the ledger: spare, or signal then settle, then confirms."""

    spared: bool
    settled: Termination | None  # None: terminate() raised before settling
    confirms: int

    @property
    def signalled(self) -> bool:
        return not self.spared

    def gone_after(self, confirms_done: int) -> bool:
        """Return True when the process is known gone after *confirms_done*."""
        if not self.signalled:
            return False
        return self.settled is Termination.EXITED or confirms_done > 0

    def __str__(self) -> str:
        if self.spared:
            head = "spare"
        else:
            head = f"signal-{self.settled.name if self.settled else 'raised'}"
        return f"{head}-confirm{self.confirms}"


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What the ledger reports after one step."""

    confirms_done: int
    account: tuple[str, ...]
    watched: bool


@dataclass(frozen=True, slots=True)
class Run:
    """A sequence replayed on a fresh ledger, with a snapshot after each step."""

    sequence: Sequence
    snapshots: tuple[Snapshot, ...]


def _replay(sequence: Sequence) -> Run:
    world = FakeWorld()
    handle = FakeHandle(world.spawn(_PID))
    ledger = KillLedger()
    if sequence.spared:
        ledger.spare(handle)
    else:
        ledger.signalling(handle)
        if sequence.settled is not None:
            ledger.settle(handle, sequence.settled)

    def snap(done: int) -> Snapshot:
        watched = handle in ledger.unconfirmed()
        return Snapshot(done, tuple(ledger.account()), watched)

    snapshots = [snap(0)]
    for done in range(1, sequence.confirms + 1):
        ledger.confirm([handle])
        snapshots.append(snap(done))
    return Run(sequence, tuple(snapshots))


def _killed(account: tuple[str, ...]) -> set[int]:
    prefix = "killed unmanaged voxd pid "
    return {
        int(pid)
        for line in account
        if line.startswith(prefix)
        for pid in line.removeprefix(prefix).split(", ")
    }


def _named(account: tuple[str, ...]) -> bool:
    return _PID in _killed(account) or any(
        re.search(rf"\bpid {_PID}\b", line) for line in account
    )


def _reported_alive(account: tuple[str, ...]) -> bool:
    return any(outcome.line(_PID) in account for outcome in _ALIVE)


def _k1(run: Run) -> bool:
    seq = run.sequence
    for snap in run.snapshots:
        if seq.signalled and not _named(snap.account):
            return False
        if seq.gone_after(snap.confirms_done) and _reported_alive(snap.account):
            return False
    return True


def _k2(run: Run) -> bool:
    if run.sequence.settled is not Termination.REFUSED:
        return True
    return all(_PID not in _killed(snap.account) for snap in run.snapshots)


def _k3(run: Run) -> bool:
    seq = run.sequence
    if not seq.signalled:
        return True
    return all(
        snap.watched is not seq.gone_after(snap.confirms_done) for snap in run.snapshots
    )


def _k4(run: Run) -> bool:
    if not run.sequence.spared:
        return True
    spared = (KillOutcome.SPARED.line(_PID),)
    return all(not snap.watched and snap.account == spared for snap in run.snapshots)


def _k5(run: Run) -> bool:
    for before, after in zip(run.snapshots, run.snapshots[1:], strict=False):
        if not before.watched and after.account != before.account:
            return False
    return True


_CHECKS: dict[LedgerInvariant, Callable[[Run], bool]] = {
    LedgerInvariant.K1_SIGNALLED_NAMED_UNTIL_GONE: _k1,
    LedgerInvariant.K2_REFUSED_NEVER_KILLED: _k2,
    LedgerInvariant.K3_UNCONFIRMED_STAYS_WATCHED: _k3,
    LedgerInvariant.K4_SPARED_NEVER_KILLED: _k4,
    LedgerInvariant.K5_CONFIRM_MOVES_ONLY_UNCONFIRMED: _k5,
}

_SETTLES: tuple[Termination | None, ...] = (None, *Termination)

SEQUENCES = (
    *(Sequence(spared=True, settled=None, confirms=n) for n in range(3)),
    *(
        Sequence(spared=False, settled=settled, confirms=n)
        for settled in _SETTLES
        for n in range(3)
    ),
)


def test_every_invariant_has_a_check() -> None:
    assert set(_CHECKS) == set(LedgerInvariant)


def test_the_sequences_cover_every_settle_outcome() -> None:
    settled = {seq.settled for seq in SEQUENCES if seq.signalled}
    assert settled == {None, *Termination}
    assert any(seq.spared for seq in SEQUENCES)


@pytest.mark.parametrize("sequence", SEQUENCES, ids=str)
@pytest.mark.parametrize("invariant", list(LedgerInvariant), ids=lambda i: i.name)
def test_invariant_holds_over_every_sequence(
    invariant: LedgerInvariant, sequence: Sequence
) -> None:
    run = _replay(sequence)
    assert _CHECKS[invariant](run), (
        f"{invariant.name} broken by {sequence}: {invariant.value}; "
        f"snapshots: {run.snapshots}"
    )
