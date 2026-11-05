"""The kill ledger's invariants, asserted by name over every transition sequence.

The ledger's transitions are finite. Each process is spared, or signalled and
then either never settled (``terminate()`` raised) or settled with one of the
``Termination`` outcomes; after that, ``confirm()`` may report processes gone
any number of times. This suite enumerates:

- every single-process sequence, with zero, one and two confirms;
- a spared process beside each signalled single-process sequence;
- two signalled processes, for every pair of settle outcomes, with the
  confirms applied to neither, to one, to the other, to both at once, and to
  each in turn.

Each sequence is replayed on a fresh ledger, and every
:class:`~punt_vox.service.kill_ledger.LedgerInvariant` is checked after every
step -- after ``signalling()`` as well as after ``settle()`` and each
``confirm()`` -- against an oracle derived from the sequence alone, not from
the ledger's own tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING

import pytest
from _lifecycle_fakes import FakeHandle, FakeWorld

from punt_vox.service.kill_ledger import KillLedger, KillOutcome, LedgerInvariant
from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from collections.abc import Callable

_A = 200
_B = 201

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
class Proc:
    """One process in a sequence: spared, or signalled and maybe settled."""

    pid: int
    spared: bool
    settled: Termination | None  # None: terminate() raised before settling

    @property
    def signalled(self) -> bool:
        return not self.spared

    def __str__(self) -> str:
        if self.spared:
            return f"{self.pid}spare"
        return f"{self.pid}{self.settled.name if self.settled else 'RAISED'}"


@dataclass(frozen=True, slots=True)
class Sequence:
    """Processes judged in order, then ``confirm()`` calls, each over some pids."""

    procs: tuple[Proc, ...]
    confirms: tuple[frozenset[int], ...]

    def __str__(self) -> str:
        steps = "-".join("c" + "+".join(map(str, sorted(c))) for c in self.confirms)
        return "-".join(map(str, self.procs)) + (f"-{steps}" if steps else "")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What the ledger reports after one step, and what the sequence had done."""

    account: tuple[str, ...]
    watched: frozenset[int]
    judged: frozenset[int]  # pids spared or signalled by this step
    settled: frozenset[int]  # pids whose settle() has run
    confirmed: frozenset[int]  # pids named in at least one confirm() so far
    after_confirm: bool  # this step was a confirm() call

    def gone(self, proc: Proc) -> bool:
        """Return True when the sequence has shown *proc* gone by this step."""
        if not proc.signalled or proc.pid not in self.judged:
            return False
        exited = proc.pid in self.settled and proc.settled is Termination.EXITED
        return exited or proc.pid in self.confirmed


@dataclass(frozen=True, slots=True)
class Run:
    """A sequence replayed on a fresh ledger, with a snapshot after each step."""

    sequence: Sequence
    snapshots: tuple[Snapshot, ...]


def _replay(sequence: Sequence) -> Run:
    world = FakeWorld()
    handles = {p.pid: FakeHandle(world.spawn(p.pid)) for p in sequence.procs}
    ledger = KillLedger()
    judged: set[int] = set()
    settled: set[int] = set()
    confirmed: set[int] = set()
    snapshots: list[Snapshot] = []

    def snap(*, after_confirm: bool = False) -> None:
        watched = frozenset(h.pid for h in ledger.unconfirmed())
        snapshots.append(
            Snapshot(
                tuple(ledger.account()),
                watched,
                frozenset(judged),
                frozenset(settled),
                frozenset(confirmed),
                after_confirm,
            )
        )

    for proc in sequence.procs:
        handle = handles[proc.pid]
        judged.add(proc.pid)
        if proc.spared:
            ledger.spare(handle)
            snap()
            continue
        ledger.signalling(handle)
        snap()  # the signalling state, before settle() can overwrite it
        if proc.settled is not None:
            ledger.settle(handle, proc.settled)
            settled.add(proc.pid)
            snap()
    for pids in sequence.confirms:
        ledger.confirm([handles[pid] for pid in pids])
        confirmed.update(pids)
        snap(after_confirm=True)
    return Run(sequence, tuple(snapshots))


_KILLED = "killed unmanaged voxd pid "


def _killed(account: tuple[str, ...]) -> set[int]:
    return {
        int(pid)
        for line in account
        if line.startswith(_KILLED)
        for pid in line.removeprefix(_KILLED).split(", ")
    }


def _lines(account: tuple[str, ...], pid: int) -> tuple[bool, tuple[str, ...]]:
    """Return what the report says about *pid*: killed?, and its own lines.

    The shared "killed" line is read through membership, not as one of the
    pid's own lines, so another pid joining it does not count as a change.
    """
    own = tuple(
        line
        for line in account
        if not line.startswith(_KILLED) and re.search(rf"\bpid {pid}\b", line)
    )
    return pid in _killed(account), own


def _named(account: tuple[str, ...], pid: int) -> bool:
    killed, own = _lines(account, pid)
    return killed or bool(own)


def _reported_alive(account: tuple[str, ...], pid: int) -> bool:
    return any(outcome.line(pid) in account for outcome in _ALIVE)


def _k1(run: Run) -> bool:
    for snap in run.snapshots:
        for proc in run.sequence.procs:
            if proc.pid not in snap.judged:
                continue
            if proc.signalled and not _named(snap.account, proc.pid):
                return False
            if snap.gone(proc) and _reported_alive(snap.account, proc.pid):
                return False
    return True


def _k2(run: Run) -> bool:
    refused = [p.pid for p in run.sequence.procs if p.settled is Termination.REFUSED]
    return all(
        pid not in _killed(snap.account) for snap in run.snapshots for pid in refused
    )


def _k3(run: Run) -> bool:
    for snap in run.snapshots:
        for proc in run.sequence.procs:
            if not proc.signalled or proc.pid not in snap.judged:
                continue
            if (proc.pid in snap.watched) is snap.gone(proc):
                return False
    return True


def _k4(run: Run) -> bool:
    alone = len(run.sequence.procs) == 1
    for snap in run.snapshots:
        for proc in run.sequence.procs:
            if not proc.spared or proc.pid not in snap.judged:
                continue
            if proc.pid in snap.watched or proc.pid in _killed(snap.account):
                return False
            spared = KillOutcome.SPARED.line(proc.pid)
            # Alone, the spared pid's report is exactly its one spared line;
            # beside another process, its line must be among the rest.
            if alone and _lines(snap.account, proc.pid) != (False, (spared,)):
                return False
            if spared not in snap.account:
                return False
    return True


def _k5(run: Run) -> bool:
    pairs = zip(run.snapshots, run.snapshots[1:], strict=False)
    for before, after in pairs:
        if not after.after_confirm:
            continue
        for proc in run.sequence.procs:
            if proc.pid in before.watched:
                continue
            if _lines(after.account, proc.pid) != _lines(before.account, proc.pid):
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


def _single() -> list[Sequence]:
    """One process: spared, or signalled with each settle; 0, 1, 2 confirms."""
    procs = [Proc(_A, spared=True, settled=None)] + [
        Proc(_A, spared=False, settled=s) for s in _SETTLES
    ]
    return [
        Sequence((proc,), (frozenset({_A}),) * n) for proc in procs for n in range(3)
    ]


def _beside_a_spared() -> list[Sequence]:
    """A spared process beside each signalled single-process sequence."""
    spared = Proc(_B, spared=True, settled=None)
    return [
        Sequence((spared, Proc(_A, spared=False, settled=s)), confirms)
        for s in _SETTLES
        for confirms in (
            (),
            (frozenset({_A}),),
            (frozenset({_A}), frozenset({_A, _B})),
        )
    ]


def _two_signalled() -> list[Sequence]:
    """Two signalled processes, every settle pair, confirms to neither/one/both."""
    patterns: tuple[tuple[frozenset[int], ...], ...] = (
        (),
        (frozenset({_A}),),
        (frozenset({_B}),),
        (frozenset({_A, _B}),),
        (frozenset({_A}), frozenset({_B})),
    )
    return [
        Sequence(
            (Proc(_A, spared=False, settled=a), Proc(_B, spared=False, settled=b)),
            confirms,
        )
        for a, b in product(_SETTLES, repeat=2)
        for confirms in patterns
    ]


SEQUENCES = (*_single(), *_beside_a_spared(), *_two_signalled())


def test_every_invariant_has_a_check() -> None:
    assert set(_CHECKS) == set(LedgerInvariant)


def test_the_sequences_cover_every_settle_outcome_and_mix() -> None:
    settles = {p.settled for seq in SEQUENCES for p in seq.procs if p.signalled}
    assert settles == {None, *Termination}
    assert any(len(seq.procs) == 2 for seq in SEQUENCES)
    mixed = [
        seq
        for seq in SEQUENCES
        if any(p.spared for p in seq.procs) and any(p.signalled for p in seq.procs)
    ]
    assert mixed


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
