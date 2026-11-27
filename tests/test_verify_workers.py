"""The verify-call fleet stops only the workers the call started, and says why not."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Protocol

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "verify_workers.py"
_SPEC = importlib.util.spec_from_file_location("verify_workers", _PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
verify_workers = importlib.util.module_from_spec(_SPEC)
sys.modules["verify_workers"] = verify_workers
_SPEC.loader.exec_module(verify_workers)


class _Result(Protocol):
    """The fields of a Cleanup these tests read."""

    @property
    def left_running(self) -> tuple[str, ...]: ...

    @property
    def problems(self) -> tuple[str, ...]: ...

    @property
    def unproven(self) -> tuple[_Row, ...]: ...


class _Row(Protocol):
    """The fields of a WorkerRow these tests read."""

    @property
    def id(self) -> str: ...

    @property
    def state(self) -> str: ...

    def describe(self) -> str: ...


class _Voxd:
    """Stand in for subprocess.run: a worker list that changes as stops land."""

    def __init__(
        self,
        workers: dict[str, str],
        *,
        stop_code: int = 0,
        list_code: int = 0,
        list_out: str | None = None,
        stop_works: bool = True,
        raises: type[BaseException] | None = None,
        raise_on: str = "",
    ) -> None:
        self.workers = workers
        self.stop_code = stop_code
        self.list_code = list_code
        self.list_out = list_out
        self.stop_works = stop_works
        self.raises = raises
        self.raise_on = raise_on
        self.commands: list[list[str]] = []

    def __call__(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        verb = command[4]
        if self.raises is not None and verb == self.raise_on:
            raise (
                self.raises(command, 30)
                if self.raises is subprocess.TimeoutExpired
                else self.raises()
            )
        if verb == "list":
            out = self.list_out
            if out is None:
                out = json.dumps(
                    [
                        {"id": i, "state": s, "task": f"task of {i}"}
                        for i, s in self.workers.items()
                    ]
                )
            return subprocess.CompletedProcess(
                command, self.list_code, out, "list broke"
            )
        if self.stop_code == 0 and self.stop_works:
            self.workers[command[-1]] = "stopped"
        return subprocess.CompletedProcess(command, self.stop_code, "", "stop broke")


def _sweep(
    monkeypatch: pytest.MonkeyPatch,
    voxd: _Voxd,
    *started: str,
    before: tuple[str, ...] = (),
) -> _Result:
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    cleanup: _Result = verify_workers.WorkerFleet(frozenset(before)).sweep(started)
    return cleanup


def test_no_started_worker_means_nothing_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"old": "ready"})
    cleanup = _sweep(monkeypatch, voxd, before=("old",))
    assert [c[4] for c in voxd.commands] == ["list"]
    assert (cleanup.left_running, cleanup.problems, cleanup.unproven) == ((), (), ())


def test_a_started_worker_the_agent_stopped_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"new": "stopped"})
    cleanup = _sweep(monkeypatch, voxd, "new")
    assert [c[4] for c in voxd.commands] == ["list"]
    assert (cleanup.left_running, cleanup.problems) == ((), ())


def test_a_started_worker_still_running_is_stopped_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"new": "ready"})
    cleanup = _sweep(monkeypatch, voxd, "new")
    assert cleanup.left_running == ("new",)
    assert cleanup.problems == ()
    assert voxd.workers["new"] == "stopped"


def test_a_worker_this_call_did_not_start_is_never_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"old": "ready", "new": "ready"})
    _sweep(monkeypatch, voxd, "new", before=("old",))
    stopped = [c[-1] for c in voxd.commands if c[4] == "stop"]
    assert stopped == ["new"]
    assert voxd.workers["old"] == "ready"


def test_a_stop_that_exits_nonzero_is_a_reported_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = _sweep(monkeypatch, _Voxd({"new": "ready"}, stop_code=1), "new")
    assert cleanup.left_running == ("new",)
    assert len(cleanup.problems) == 1
    assert "new" in cleanup.problems[0]
    assert "stop broke" in cleanup.problems[0]


def test_a_stop_that_times_out_is_a_reported_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"new": "ready"}, raises=subprocess.TimeoutExpired, raise_on="stop")
    cleanup = _sweep(monkeypatch, voxd, "new")
    assert len(cleanup.problems) == 1
    assert "timed out" in cleanup.problems[0]


def test_a_stop_that_leaves_the_worker_running_is_a_reported_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = _sweep(monkeypatch, _Voxd({"new": "ready"}, stop_works=False), "new")
    assert len(cleanup.problems) == 1
    assert "still running" in cleanup.problems[0]


@pytest.mark.parametrize(
    ("voxd", "expected"),
    [
        (_Voxd({}, list_code=1), "list broke"),
        (_Voxd({}, list_out=""), "unreadable"),
        (_Voxd({}, list_out='{"id": "new"}'), "not a list"),
        (_Voxd({}, list_out="[1, 2]"), "not a list of workers"),
        (
            _Voxd({}, raises=subprocess.TimeoutExpired, raise_on="list"),
            "timed out",
        ),
        (_Voxd({}, raises=FileNotFoundError, raise_on="list"), "FileNotFoundError"),
    ],
)
def test_a_list_that_fails_after_the_call_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, voxd: _Voxd, expected: str
) -> None:
    cleanup = _sweep(monkeypatch, voxd, "new")
    assert len(cleanup.problems) == 1
    assert expected in cleanup.problems[0]


def test_every_voxd_command_runs_under_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"new": "ready"})
    _sweep(monkeypatch, voxd, "new")
    assert voxd.commands
    assert all(c[:2] == ["timeout", "30"] for c in voxd.commands)


def test_a_new_worker_the_trace_does_not_name_is_reported_and_not_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"old": "ready", "new": "ready", "other": "ready"})
    cleanup = _sweep(monkeypatch, voxd, "new", before=("old",))
    assert [row.id for row in cleanup.unproven] == ["other"]
    assert [c[-1] for c in voxd.commands if c[4] == "stop"] == ["new"]
    assert voxd.workers["other"] == "ready"


def test_a_worker_that_was_there_before_the_call_is_never_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = _sweep(monkeypatch, _Voxd({"old": "ready"}), before=("old",))
    assert cleanup.unproven == ()


def test_an_unproven_worker_is_named_with_its_state_task_and_how_to_stop_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = _sweep(monkeypatch, _Voxd({"w1": "ready"}))
    text = cleanup.unproven[0].describe()
    assert "w1" in text
    assert "ready" in text
    assert "task of w1" in text
    assert "vox worker stop w1" in text
    assert "not stopped" in text


def test_a_long_task_is_clipped_in_the_description() -> None:
    row = verify_workers.WorkerRow("w1", "ready", "x" * 500)
    assert len(row.describe()) < 400


def test_observing_the_workers_before_the_call_names_a_failed_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_workers.subprocess, "run", _Voxd({}, list_code=1))
    with pytest.raises(ValueError, match="list broke"):
        verify_workers.WorkerFleet.observe()


def test_observing_records_the_workers_that_exist_before_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"old": "ready"})
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    fleet = verify_workers.WorkerFleet.observe()
    assert fleet.sweep(()).unproven == ()


def test_a_trace_naming_a_worker_that_existed_before_the_call_stops_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voxd = _Voxd({"old": "ready"})
    cleanup = _sweep(monkeypatch, voxd, "old", before=("old",))
    assert [c[4] for c in voxd.commands] == ["list"]
    assert voxd.workers["old"] == "ready"
    assert len(cleanup.problems) == 1
    assert "existed before the call" in cleanup.problems[0]
