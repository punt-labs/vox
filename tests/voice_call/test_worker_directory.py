from __future__ import annotations

import pytest

from punt_vox.voxd.voice_call.worker_directory import WorkerDirectory

from .fakes import FakeWorkers, payload


async def test_exact_match_wins_over_prefix_and_lookup_reads_the_live_roster() -> None:
    workers = FakeWorkers()
    directory = WorkerDirectory(workers)
    workers.known = [payload("abcd"), payload("abcd1234")]
    assert (await directory.find("abcd"))["id"] == "abcd"
    workers.known = [payload("abcd1234")]
    assert (await directory.find("abcd"))["id"] == "abcd1234"


@pytest.mark.parametrize("wanted", ["", "a", "abcd", "missing"])
async def test_empty_short_ambiguous_and_unknown_ids_are_refused(wanted: str) -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234"), payload("abcd9999")]
    with pytest.raises(ValueError, match="no single worker"):
        await WorkerDirectory(workers).find(wanted)


async def test_empty_roster_refuses_lookup() -> None:
    with pytest.raises(ValueError, match="no single worker"):
        await WorkerDirectory(FakeWorkers()).find("abcd")
