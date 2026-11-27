"""The verify-call judge reads a call's report and names each failed expectation."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest

from punt_vox.voxd.voice_call.spoken_answer import SpokenAnswer

_TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify_workers = _load("verify_workers")  # verify_call imports it by name.
verify_judge = _load("verify_judge")  # verify_call imports it by name.
verify_call = _load("verify_call")
_CLEAN = verify_workers.Cleanup(started=("abcd1234",), left_running=(), problems=())

_GOOD = """\
call ended by caller | 2 tool calls | agent: Hi
agent: Hi, it's Vox. What are we working on?
user: What does the pallets flask repository say about application factories?
agent: One moment.
tool: read_docs({'repo': 'pallets/flask'}) -> ok
agent: Flask lets you build the app inside a function, which helps testing.
user: Search the web for the weather in Tokyo.
tool: search_web({}) -> ok
agent: It is sunny in Tokyo.
user: Please start a coding worker in this repository.
tool: start_worker({'task': 'git status'}) -> ok
agent: Started it.
tool: worker_status({'worker_id': 'abcd1234'}) -> ok
agent: It is running git status.
tool: stop_worker({'worker_id': 'abcd1234'}) -> ok
user: Give me a minute, I need to think.
ended by caller
trace: /tmp/call.jsonl
"""


def _verdicts(
    report: str, *, search_key: bool = True, output: str = ""
) -> dict[str, str]:
    judged = verify_judge.Judge(
        report,
        output,
        search_key=search_key,
        launch_exit=0,
        trace=_TRACE,
        cleanup=_CLEAN,
    ).verdicts()
    return {v.name: v.status for v in judged}


def test_a_good_call_passes_every_expectation() -> None:
    assert set(_verdicts(_GOOD).values()) == {"PASS"}


def test_the_old_workers_opener_fails_the_greeting() -> None:
    old = _GOOD.replace(
        "Hi, it's Vox. What are we working on?",
        "Hello, this is Vox. I start, steer, check and stop coding workers in vox.",
    )
    assert _verdicts(old)["greeting"] == "FAIL"


def test_a_missing_or_failed_docs_call_fails() -> None:
    assert _verdicts(_GOOD.replace("read_docs", "other"))["docs"] == "FAIL"
    assert (
        _verdicts(
            _GOOD.replace(
                "read_docs({'repo': 'pallets/flask'}) -> ok", "read_docs({}) -> error"
            )
        )["docs"]
        == "FAIL"
    )


def test_markdown_in_an_agent_reply_fails_plain_words() -> None:
    marked = _GOOD.replace("Flask lets you", "## Answer Flask lets you")
    assert _verdicts(marked)["plain words"] == "FAIL"


def test_search_without_a_key_is_a_distinct_skip_never_a_pass() -> None:
    skipped = _verdicts(
        _GOOD.replace("tool: search_web({}) -> ok\n", ""), search_key=False
    )
    assert skipped["search"] == "SKIP"


def test_search_with_a_key_must_call_the_tool() -> None:
    assert (
        _verdicts(_GOOD.replace("tool: search_web({}) -> ok\n", ""))["search"] == "FAIL"
    )


@pytest.mark.parametrize(
    "nag", ["Are you there?", "Are you still there?", "still there"]
)
def test_a_nag_during_silence_fails(nag: str) -> None:
    nagged = _GOOD.replace("ended by caller", f"agent: {nag}\nended by caller")
    assert _verdicts(nagged)["silence"] == "FAIL"


def test_a_runtime_warning_on_hang_up_fails_a_clean_end() -> None:
    warned = _verdicts(_GOOD, output="RuntimeWarning: coroutine x was never awaited")
    assert warned["clean end"] == "FAIL"


def test_a_call_that_never_ended_fails_a_clean_end() -> None:
    assert _verdicts(_GOOD.replace("ended by caller\n", ""))["clean end"] == "FAIL"


_STATUS = (Path(__file__).parent / "fixtures" / "vox_call_status_ended.txt").read_text()


_WORKER_STATUS = (
    Path(__file__).parent / "fixtures" / "vox_call_status_worker.txt"
).read_text()


def test_the_real_status_text_with_worker_turns_passes_every_expectation() -> None:
    assert set(_verdicts(_WORKER_STATUS).values()) == {"PASS"}


def test_a_real_status_text_without_worker_turns_fails_only_the_worker_ones() -> None:
    # The older capture predates the worker turns, so it holds no worker call.
    verdicts = _verdicts(_STATUS)
    assert verdicts.pop("worker status") == "FAIL"
    assert verdicts.pop("worker cleanup") == "FAIL"
    assert set(verdicts.values()) == {"PASS"}


def test_markdown_on_a_continuation_line_of_an_agent_reply_fails_plain_words() -> None:
    marked = _STATUS.replace("It covers tools", "## It covers tools")
    assert _verdicts(marked)["plain words"] == "FAIL"


def test_a_nag_on_a_continuation_line_fails_silence() -> None:
    nagged = _STATUS.replace("It covers tools", "Are you still there? It covers tools")
    assert _verdicts(nagged)["silence"] == "FAIL"


def test_the_first_agent_turn_is_judged_even_after_a_live_header_line() -> None:
    assert (
        verify_judge.Judge(_STATUS, "", search_key=True, launch_exit=0)
        .verdicts()[0]
        .status
        == "PASS"
    )


def test_the_script_starts_with_a_silent_lead_in_so_the_greeting_is_not_clipped() -> (
    None
):
    first = verify_call.script_plan(search=True)[0]
    assert first.text == ""
    assert first.pause_s >= 6


def test_the_docs_turn_is_unmistakable_to_speech_recognition() -> None:
    texts = [t.text for t in verify_call.script_plan(search=True)]
    assert (
        "What is the Python SDK repository for the Model Context Protocol? "
        "Look it up in the docs."
    ) in texts
    assert not any("flask" in t.lower() for t in texts)


def test_without_search_the_plan_leaves_out_the_search_turn() -> None:
    assert not any(
        "search the web" in t.text for t in verify_call.script_plan(search=False)
    )


_LIVE = (Path(__file__).parent / "fixtures" / "vox_call_status_live.txt").read_text()


def test_a_live_status_is_not_final() -> None:
    assert not verify_judge.is_final(_LIVE)


def test_an_ended_status_is_final() -> None:
    assert verify_judge.is_final(_STATUS)


def test_a_failed_status_is_final() -> None:
    assert verify_judge.is_final("call failed | 0 tool calls\nerror: boom\n")


@pytest.mark.parametrize("text", ["", "call idle", "call connecting | 0 tool calls"])
def test_empty_idle_or_connecting_status_is_not_final(text: str) -> None:
    assert not verify_judge.is_final(text)


def test_a_live_status_fails_the_clean_end_even_if_it_looks_complete() -> None:
    verdicts = {
        v.name: v
        for v in verify_judge.Judge(
            _LIVE, "", search_key=True, launch_exit=0
        ).verdicts()
    }
    assert verdicts["clean end"].status == "FAIL"
    assert "still live" in verdicts["clean end"].why


_TRACE: list[dict[str, object]] = [
    {"ms": 10, "dir": "recv", "type": "agent_response", "detail": {}},
    {
        "ms": 20,
        "dir": "recv",
        "type": "client_tool_call",
        "detail": {"tool_name": "read_docs"},
    },
    {
        "ms": 90,
        "dir": "recv",
        "type": "agent_tool_response",
        "detail": {"tool_name": "read_docs", "is_error": False},
    },
    {"ms": 120, "dir": "recv", "type": "agent_response", "detail": {}},
    {
        "ms": 200,
        "dir": "recv",
        "type": "agent_tool_response",
        "detail": {"tool_name": "worker_status", "is_error": False},
    },
    {
        "ms": 150,
        "dir": "note",
        "type": "worker_started",
        "detail": {"worker_id": "abcd1234"},
    },
    {
        "ms": 206,
        "dir": "note",
        "type": "worker_status_result",
        "detail": {
            "worker_id": "abcd1234",
            "state": "working",
            "lifecycle": "ready",
            "activity": {"files": [], "tool_in_flight": "Bash"},
        },
    },
    {"ms": 300, "dir": "recv", "type": "agent_response", "detail": {}},
    {"ms": 190, "dir": "recv", "type": "user_transcript", "detail": {}},
    {"ms": 400, "dir": "recv", "type": "user_transcript", "detail": {}},
]


def _with_trace(trace: list[dict[str, object]] | None) -> dict[str, str]:
    judged = verify_judge.Judge(
        _GOOD, "", search_key=True, launch_exit=0, trace=trace, cleanup=_CLEAN
    ).verdicts()
    return {v.name: v.status for v in judged}


def test_docs_passes_when_an_agent_reply_follows_the_tool_result() -> None:
    assert _with_trace(_TRACE)["docs"] == "PASS"


def test_docs_fails_when_the_agent_said_nothing_after_the_tool_result() -> None:
    assert _with_trace(_TRACE[:3])["docs"] == "FAIL"


def test_docs_fails_when_the_only_reply_came_before_the_tool_result() -> None:
    assert _with_trace([_TRACE[0], _TRACE[1], _TRACE[2]])["docs"] == "FAIL"


def test_docs_fails_when_there_is_no_trace_to_confirm_a_reply() -> None:
    assert _with_trace(None)["docs"] == "FAIL"


def test_docs_fails_when_the_tool_result_was_an_error() -> None:
    errored = [dict(row) for row in _TRACE]
    errored[2] = {**errored[2], "detail": {"tool_name": "read_docs", "is_error": True}}
    assert _with_trace(errored)["docs"] == "FAIL"


def test_a_quiet_agent_through_the_whole_silence_passes() -> None:
    quiet = _GOOD.replace(
        "user: Give me a minute, I need to think.\n",
        "user: Give me a minute, I need to think.\n",
    )
    assert _verdicts(quiet)["silence"] == "PASS"


def _which(tool: str) -> str:
    return f"/bin/{tool}"


class _Recorder:
    """Stand in for subprocess.run: record every command, answer status as live."""

    def __init__(self, status: str) -> None:
        self.status = status
        self.commands: list[list[str]] = []

    def __call__(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        out = self.status if command[-2:] == ["call", "status"] else ""
        return subprocess.CompletedProcess(command, 0, out, "")


def test_an_existing_live_call_is_refused_and_left_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = _Recorder(_LIVE)
    monkeypatch.setattr(verify_call.subprocess, "run", recorder)
    monkeypatch.setattr(verify_call.shutil, "which", _which)
    assert verify_call.main() != 0
    assert "already live" in capsys.readouterr().out
    assert not any("stop" in command for command in recorder.commands)
    assert not any("--script" in command for command in recorder.commands)


def test_a_run_that_placed_no_call_never_stops_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(_LIVE)
    monkeypatch.setattr(verify_call.subprocess, "run", recorder)
    verify_call.CallRun(tmp_path)._stop("")
    assert recorder.commands == []


def test_the_trace_named_by_the_status_text_is_read(tmp_path: Path) -> None:
    trace = tmp_path / "call.jsonl"
    trace.write_text('{"ms": 1, "type": "agent_response", "detail": {}}\n')
    rows = verify_call.load_trace(f"call ended by caller\ntrace: {trace}\n")
    assert rows == [{"ms": 1, "type": "agent_response", "detail": {}}]
    assert verify_call.load_trace("call ended by caller\n") is None
    assert verify_call.load_trace(f"trace: {tmp_path / 'missing'}\n") is None


def _worker_status(
    report: str = _GOOD, trace: list[dict[str, object]] | None = _TRACE
) -> str:
    judged = verify_judge.Judge(
        report, "", search_key=True, launch_exit=0, trace=trace, cleanup=_CLEAN
    ).verdicts()
    return str(next(v.status for v in judged if v.name == "worker status"))


def _status_row(**detail: object) -> dict[str, object]:
    reading = {"worker_id": "abcd1234", **detail}
    return {"ms": 206, "dir": "note", "type": "worker_status_result", "detail": reading}


def _trace_with(row: dict[str, object]) -> list[dict[str, object]]:
    return [r for r in _TRACE if r.get("type") != "worker_status_result"] + [row]


def test_a_worker_started_and_read_with_a_valid_state_and_activity_passes() -> None:
    assert _worker_status() == "PASS"


def test_worker_status_fails_when_no_worker_was_started() -> None:
    report = _GOOD.replace("start_worker", "other")
    assert _worker_status(report) == "FAIL"


def test_worker_status_fails_when_the_agent_never_asked_the_worker() -> None:
    report = _GOOD.replace("tool: worker_status", "tool: other")
    assert _worker_status(report) == "FAIL"


def test_worker_status_fails_when_the_tool_errored() -> None:
    report = _GOOD.replace(
        "worker_status({'worker_id': 'abcd1234'}) -> ok", "worker_status({}) -> error"
    )
    assert _worker_status(report) == "FAIL"


def test_worker_status_fails_without_a_trace_to_read_the_result_from() -> None:
    assert _worker_status(trace=None) == "FAIL"


def test_worker_status_fails_when_the_trace_holds_no_result() -> None:
    bare = [r for r in _TRACE if r.get("type") != "worker_status_result"]
    assert _worker_status(trace=bare) == "FAIL"


@pytest.mark.parametrize("state", ["", "busy", "ready", None, 3])
def test_worker_status_fails_on_a_state_word_the_agent_may_not_speak(
    state: object,
) -> None:
    row = _status_row(state=state, lifecycle="ready", activity={"files": []})
    assert _worker_status(trace=_trace_with(row)) == "FAIL"


@pytest.mark.parametrize("state", ["working", "blocked", "idle", "finished"])
def test_worker_status_passes_every_spoken_state_word(state: str) -> None:
    row = _status_row(state=state, lifecycle="ready", activity={"files": []})
    assert _worker_status(trace=_trace_with(row)) == "PASS"


@pytest.mark.parametrize("activity", [None, "busy", [], 3])
def test_worker_status_fails_when_activity_is_not_an_object(activity: object) -> None:
    row = _status_row(state="working", lifecycle="ready", activity=activity)
    assert _worker_status(trace=_trace_with(row)) == "FAIL"


def test_worker_status_fails_without_a_lifecycle() -> None:
    row = _status_row(state="working", lifecycle="", activity={})
    assert _worker_status(trace=_trace_with(row)) == "FAIL"


def test_worker_status_fails_when_no_reply_follows_the_result() -> None:
    cut = [r for r in _TRACE if r["ms"] != 300]
    assert _worker_status(trace=cut) == "FAIL"


def test_worker_status_fails_for_a_worker_this_call_never_started() -> None:
    row = _status_row(
        worker_id="old-worker", state="working", lifecycle="ready", activity={}
    )
    assert _worker_status(trace=_trace_with(row)) == "FAIL"


def test_worker_status_fails_when_the_trace_shows_no_worker_started() -> None:
    bare = [r for r in _TRACE if r.get("type") != "worker_started"]
    assert _worker_status(trace=bare) == "FAIL"


def test_the_plan_confirms_the_start_before_asking_what_the_worker_is_doing() -> None:
    texts = [t.text for t in verify_call.script_plan(search=False)]
    start = next(i for i, t in enumerate(texts) if "start a coding worker" in t)
    assert texts[start + 1] == "Yes."
    status = next(i for i, t in enumerate(texts) if "what the worker is doing" in t)
    assert start + 1 < status


def test_the_start_confirmation_pauses_long_enough_for_the_worker_to_launch() -> None:
    plan = verify_call.script_plan(search=False)
    start = next(i for i, t in enumerate(plan) if "start a coding worker" in t.text)
    assert plan[start + 1].pause_s >= 45


def test_the_stop_request_names_the_worker_it_started_then_confirms() -> None:
    texts = [t.text for t in verify_call.script_plan(search=False)]
    stop = next(i for i, t in enumerate(texts) if "stop" in t and "worker" in t)
    assert "git status worker you just started" in texts[stop]
    assert texts[stop + 1] == "Yes."


def test_every_scripted_confirmation_is_a_yes_the_daemon_will_accept() -> None:
    texts = [t.text for t in verify_call.script_plan(search=False)]
    requests = [
        i
        for i, t in enumerate(texts)
        if "start a coding worker" in t or "stop the" in t
    ]
    assert len(requests) == 2
    assert [SpokenAnswer.of(texts[i + 1]) for i in requests] == ["yes", "yes"]


def test_the_worker_task_is_read_only() -> None:
    texts = " ".join(t.text for t in verify_call.script_plan(search=False))
    assert "must not change any file" in texts


def _stub_script(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Replace the audio-generating script step with an empty script path."""

    def script(self: object, *, search: bool) -> Path:
        return directory / "script.jsonl"

    monkeypatch.setattr(verify_call.CallRun, "_script", script)


def _stub_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every status text read as the synthetic trace."""

    def load(status: str) -> list[dict[str, object]]:
        return _TRACE

    monkeypatch.setattr(verify_call, "load_trace", load)


def _cleanup_verdict(cleanup: object, report: str = _GOOD) -> tuple[str, str]:
    judged = verify_judge.Judge(
        report, "", search_key=True, launch_exit=0, trace=_TRACE, cleanup=cleanup
    ).verdicts()
    verdict = next(v for v in judged if v.name == "worker cleanup")
    return str(verdict.status), str(verdict.why)


def test_a_started_worker_the_agent_stopped_passes_worker_cleanup() -> None:
    assert _cleanup_verdict(_CLEAN)[0] == "PASS"


def test_worker_cleanup_fails_without_a_cleanup_report() -> None:
    assert _cleanup_verdict(None)[0] == "FAIL"


def test_a_worker_the_agent_left_running_fails_worker_cleanup() -> None:
    left = verify_workers.Cleanup(("abcd1234",), ("abcd1234",), ())
    status, why = _cleanup_verdict(left)
    assert status == "FAIL"
    assert "abcd1234" in why


def test_a_cleanup_problem_fails_worker_cleanup_and_is_shown() -> None:
    broken = verify_workers.Cleanup(("abcd1234",), (), ("vox worker list failed: x",))
    status, why = _cleanup_verdict(broken)
    assert status == "FAIL"
    assert "vox worker list failed: x" in why


def test_worker_cleanup_fails_when_stop_worker_never_succeeded() -> None:
    report = _GOOD.replace("stop_worker({'worker_id': 'abcd1234'}) -> ok", "x() -> ok")
    assert _cleanup_verdict(_CLEAN, report)[0] == "FAIL"


def test_worker_cleanup_fails_when_the_call_started_no_worker() -> None:
    assert _cleanup_verdict(verify_workers.Cleanup((), (), ()))[0] == "FAIL"


def _report(trace: Path | None) -> str:
    """Return a good end-of-call report whose trace line names ``trace``."""
    line = f"trace: {trace}\n" if trace else ""
    return _GOOD.replace("trace: /tmp/call.jsonl\n", line)


class _Voxd:
    """Stand in for subprocess.run around one placed call and its workers."""

    def __init__(
        self, trace: Path | None, before: dict[str, str], after: dict[str, str]
    ) -> None:
        self.trace = trace
        self.before = before
        self.after = after
        self.launched = False
        self.launch_code = 0
        self.launch_out: str | None = None  # None: the report of ``trace``.
        self.status_out: str | None = None  # None: the report of ``trace``.
        self.before_code = 0
        self.after_code = 0
        self.pre_status = "call idle\n"  # What status says before the launch.
        self.post_statuses: list[str] = []  # Answers after the launch, in order.
        self.stop_call_code = 0
        self.launch_raises: BaseException | None = None
        self.commands: list[list[str]] = []

    def __call__(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[-2:] == ["call", "status"]:
            out = _report(self.trace) if self.status_out is None else self.status_out
            if self.launched and self.post_statuses:
                out = (
                    self.post_statuses.pop(0)
                    if len(self.post_statuses) > 1
                    else self.post_statuses[0]
                )
            out = out if self.launched else self.pre_status
            return subprocess.CompletedProcess(command, 0, out, "")
        if command[-2:] == ["call", "stop"]:
            return subprocess.CompletedProcess(
                command, self.stop_call_code, "", "stop broke"
            )
        if "--script" in command:
            self.launched = True
            if self.launch_raises is not None:
                raise self.launch_raises
            out = _report(self.trace) if self.launch_out is None else self.launch_out
            err = "Call unavailable" if self.launch_code else ""
            return subprocess.CompletedProcess(command, self.launch_code, out, err)
        if command[-2:] == ["worker", "list"]:
            code = self.after_code if self.launched else self.before_code
            workers = self.after if self.launched else self.before
            rows = [
                {"id": i, "state": s, "task": f"task of {i}"}
                for i, s in workers.items()
            ]
            out = json.dumps(rows) if code == 0 else ""
            return subprocess.CompletedProcess(command, code, out, "no voxd")
        if command[3:5] == ["worker", "stop"]:
            self.after[command[-1]] = "stopped"
        return subprocess.CompletedProcess(command, 0, "", "")

    def stops(self) -> list[str]:
        return [c[-1] for c in self.commands if c[3:5] == ["worker", "stop"]]

    def call_stops(self) -> list[list[str]]:
        return [c for c in self.commands if c[-2:] == ["call", "stop"]]

    def scripts(self) -> list[list[str]]:
        return [c for c in self.commands if "--script" in c]


def _trace_file(directory: Path, *worker_ids: str) -> Path:
    path = directory / "call.jsonl"
    rows = [
        {"ms": 1, "dir": "note", "type": "worker_started", "detail": {"worker_id": i}}
        for i in worker_ids
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


class _Row(Protocol):
    """The fields of a WorkerRow these tests read."""

    @property
    def id(self) -> str: ...


class _Cleanup(Protocol):
    """The fields of a Cleanup these tests read."""

    @property
    def started(self) -> tuple[str, ...]: ...

    @property
    def left_running(self) -> tuple[str, ...]: ...

    @property
    def problems(self) -> tuple[str, ...]: ...

    @property
    def unproven(self) -> tuple[_Row, ...]: ...


def _place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, voxd: _Voxd
) -> tuple[_Cleanup, str]:
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    _stub_script(monkeypatch, tmp_path)
    run = verify_call.CallRun(tmp_path)
    status, _ = run.place(search=False)
    cleanup: _Cleanup = run.cleanup
    return cleanup, str(status)


def test_a_call_stops_only_the_workers_its_trace_says_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(
        _trace_file(tmp_path, "new"), {"old": "ready"}, {"old": "ready", "new": "ready"}
    )
    cleanup, _ = _place(tmp_path, monkeypatch, voxd)
    assert voxd.stops() == ["new"]
    assert cleanup.left_running == ("new",)
    assert cleanup.unproven == ()


def test_a_worker_started_without_a_trace_note_is_named_and_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(_trace_file(tmp_path), {}, {"w1": "ready"})
    cleanup, _ = _place(tmp_path, monkeypatch, voxd)
    assert voxd.stops() == []
    assert [r.id for r in cleanup.unproven] == ["w1"]


def test_a_call_cancelled_mid_launch_reports_the_start_and_names_new_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(None, {}, {"w2": "starting"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    cleanup, status = _place(tmp_path, monkeypatch, voxd)
    assert status == ""
    assert voxd.stops() == []
    assert "timed out" in cleanup.problems[0]
    assert [r.id for r in cleanup.unproven] == ["w2"]


def test_another_sessions_worker_started_in_the_window_is_named_not_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "new"), {}, {"new": "ready", "other": "ready"})
    cleanup, _ = _place(tmp_path, monkeypatch, voxd)
    assert voxd.stops() == ["new"]
    assert [r.id for r in cleanup.unproven] == ["other"]
    assert voxd.after["other"] == "ready"


def test_a_refused_launch_never_sweeps_the_previous_calls_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = tmp_path / "older"
    older.mkdir()
    voxd = _Voxd(None, {"old-worker": "ready"}, {"old-worker": "ready"})
    voxd.launch_code = 1
    voxd.launch_out = ""
    voxd.pre_status = _report(_trace_file(older, "old-worker"))
    voxd.status_out = voxd.pre_status
    cleanup, status = _place(tmp_path, monkeypatch, voxd)
    assert voxd.stops() == []
    assert status == ""
    assert "exited 1 without a report" in cleanup.problems[0]
    assert "Call unavailable" in cleanup.problems[0]


def test_a_status_that_belongs_to_another_call_is_not_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    mine = _trace_file(tmp_path, "new")
    voxd = _Voxd(mine, {}, {"new": "stopped"})
    voxd.status_out = _report(_trace_file(other, "someone-elses"))
    cleanup, status = _place(tmp_path, monkeypatch, voxd)
    assert str(mine) in status
    assert "someone-elses" not in status
    assert cleanup.started == ("new",)


def test_the_workers_are_listed_before_the_call_is_placed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(_trace_file(tmp_path), {}, {})
    voxd.before_code = 1
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    _stub_script(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="no voxd"):
        verify_call.CallRun(tmp_path).place(search=False)
    assert voxd.scripts() == []


def _run_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, voxd: _Voxd, *, search: bool
) -> int:
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    monkeypatch.setattr(verify_call.shutil, "which", _which)
    monkeypatch.setattr(verify_call, "_search_configured", lambda: search)
    _stub_script(monkeypatch, tmp_path)
    _stub_trace(monkeypatch)
    return int(verify_call.main())


def test_a_worker_list_failing_after_the_call_does_not_hide_the_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "abcd1234"), {}, {})
    voxd.after_code = 1
    assert _run_main(monkeypatch, tmp_path, voxd, search=False) == 1
    out = capsys.readouterr().out
    assert "PASS greeting" in out
    assert "FAIL worker cleanup" in out
    assert "no voxd" in out


def test_a_clean_run_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "abcd1234"), {}, {"abcd1234": "stopped"})
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 0
    assert "PASS worker cleanup" in capsys.readouterr().out


def test_a_leftover_worker_makes_the_run_exit_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "abcd1234"), {}, {"abcd1234": "ready"})
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 1
    assert "left" in capsys.readouterr().out


def test_an_unproven_worker_fails_the_run_and_names_how_to_stop_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(
        _trace_file(tmp_path, "abcd1234"),
        {},
        {"abcd1234": "stopped", "w9": "ready"},
    )
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 1
    out = capsys.readouterr().out
    assert "FAIL worker cleanup" in out
    assert "vox worker stop w9" in out
    assert voxd.stops() == []


def test_a_workers_list_that_fails_before_the_call_stops_the_run_unplaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path), {}, {})
    voxd.before_code = 1
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 4
    assert "FAIL setup" in capsys.readouterr().out
    assert voxd.scripts() == []


def test_a_reply_after_the_next_caller_turn_is_not_the_spoken_status() -> None:
    late = [r for r in _TRACE if r["ms"] != 300]
    late.append({"ms": 450, "dir": "recv", "type": "agent_response", "detail": {}})
    assert _worker_status(trace=late) == "FAIL"


def test_a_reply_when_no_caller_turn_follows_is_the_spoken_status() -> None:
    trace = [r for r in _TRACE if r["ms"] not in (300, 400)]
    trace.append({"ms": 450, "dir": "recv", "type": "agent_response", "detail": {}})
    assert _worker_status(trace=trace) == "PASS"


@pytest.mark.parametrize(
    "activity",
    [{}, {"tool_in_flight": "Bash"}, {"files": "a.py"}, {"files": [1]}],
)
def test_worker_status_fails_when_activity_has_no_files_list(
    activity: dict[str, object],
) -> None:
    row = _status_row(state="working", lifecycle="ready", activity=activity)
    assert _worker_status(trace=_trace_with(row)) == "FAIL"


def test_an_unproven_worker_fails_worker_cleanup_and_is_named() -> None:
    row = verify_workers.WorkerRow("w9", "ready", "do things")
    status, why = _cleanup_verdict(
        verify_workers.Cleanup(("abcd1234",), (), (), (row,))
    )
    assert status == "FAIL"
    assert "w9" in why
    assert "vox worker stop w9" in why


def _live(trace: Path) -> str:
    return _report(trace).replace("call ended by caller", "call live", 1)


def test_a_finished_launch_has_nothing_left_to_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "new"), {}, {"new": "stopped"})
    other = tmp_path / "foreign"
    other.mkdir()
    voxd.status_out = _live(_trace_file(other, "x"))
    _place(tmp_path, monkeypatch, voxd)
    assert voxd.call_stops() == []


def test_a_live_call_with_a_foreign_trace_is_never_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "foreign"
    other.mkdir()
    foreign = _trace_file(other, "x")
    voxd = _Voxd(None, {}, {})
    voxd.pre_status = _report(foreign)
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.status_out = _live(foreign)
    _place(tmp_path, monkeypatch, voxd)
    assert voxd.call_stops() == []


def test_a_timed_out_launch_still_stops_its_own_live_call_and_its_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = _trace_file(tmp_path, "new")
    voxd = _Voxd(mine, {}, {"new": "ready"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.post_statuses = [_live(mine), _live(mine), _report(mine)]
    cleanup, status = _place(tmp_path, monkeypatch, voxd)
    assert len(voxd.call_stops()) == 1
    assert voxd.stops() == ["new"]
    assert cleanup.started == ("new",)
    assert cleanup.problems == ()
    assert str(mine) in status


def test_a_timed_out_launch_whose_call_already_ended_recovers_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = _trace_file(tmp_path, "new")
    voxd = _Voxd(mine, {}, {"new": "exited"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    cleanup, status = _place(tmp_path, monkeypatch, voxd)
    assert voxd.call_stops() == []
    assert str(mine) in status
    assert cleanup.started == ("new",)
    assert cleanup.unproven == ()


def test_the_launch_timeout_is_the_script_plus_a_short_drain() -> None:
    for search in (True, False):
        plan = verify_call.script_plan(search=search)
        total = sum(turn.pause_s for turn in plan)
        timeout = verify_call.launch_timeout_s(search=search)
        assert total + 30 <= timeout <= total + 90


def test_a_scripted_call_that_runs_to_the_timeout_is_a_clean_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _trace_file(tmp_path, "abcd1234")
    voxd = _Voxd(trace, {}, {"abcd1234": "stopped"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.post_statuses = [_live(trace), _live(trace), _report(trace)]
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 0
    out = capsys.readouterr().out
    assert "PASS clean end" in out
    assert "PASS worker cleanup" in out


def test_a_call_that_never_ends_after_the_timeout_still_fails_clean_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _trace_file(tmp_path, "abcd1234")
    voxd = _Voxd(trace, {}, {"abcd1234": "stopped"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.post_statuses = [_live(trace)]
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 1
    assert "FAIL clean end" in capsys.readouterr().out


@pytest.mark.parametrize("code", [1, 2, 130])
def test_any_other_non_zero_launch_exit_still_fails_clean_end(code: int) -> None:
    judged = verify_judge.Judge(
        _GOOD, "", search_key=True, launch_exit=code, trace=_TRACE, cleanup=_CLEAN
    ).verdicts()
    assert next(v for v in judged if v.name == "clean end").status == "FAIL"


def test_the_timeout_exit_of_a_scripted_launch_is_an_expected_end() -> None:
    judged = verify_judge.Judge(
        _GOOD, "", search_key=True, launch_exit=124, trace=_TRACE, cleanup=_CLEAN
    ).verdicts()
    assert next(v for v in judged if v.name == "clean end").status == "PASS"


def test_the_launch_runs_under_that_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "new"), {}, {"new": "stopped"})
    _place(tmp_path, monkeypatch, voxd)
    command = voxd.scripts()[0]
    assert command[:2] == [
        "timeout",
        str(verify_call.launch_timeout_s(search=False)),
    ]


def test_a_failed_call_stop_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = _trace_file(tmp_path)
    voxd = _Voxd(mine, {}, {})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.stop_call_code = 1
    voxd.status_out = _live(mine)
    cleanup, _ = _place(tmp_path, monkeypatch, voxd)
    assert any("vox call stop" in p and "stop broke" in p for p in cleanup.problems)


def test_a_status_trace_equal_to_the_pre_launch_one_is_not_this_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = _trace_file(tmp_path, "old-worker")
    voxd = _Voxd(None, {"old-worker": "ready"}, {"old-worker": "ready"})
    voxd.pre_status = _report(older)
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.status_out = _report(older)
    cleanup, status = _place(tmp_path, monkeypatch, voxd)
    assert voxd.stops() == []
    assert voxd.call_stops() == []
    assert status == ""
    assert cleanup.started == ()


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired(["vox"], 435), FileNotFoundError("vox")],
)
def test_a_launch_that_raises_still_reports_the_cleanup_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: BaseException,
) -> None:
    voxd = _Voxd(None, {}, {"w7": "ready"})
    voxd.launch_raises = error
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 1
    out = capsys.readouterr().out
    assert "FAIL worker cleanup" in out
    assert type(error).__name__ in out
    assert "w7" in out
    assert voxd.stops() == []


def test_a_script_that_cannot_be_built_fails_the_run_with_its_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(None, {}, {})

    def broken(self: object, *, search: bool) -> Path:
        raise subprocess.CalledProcessError(1, ["say"])

    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    monkeypatch.setattr(verify_call.shutil, "which", _which)
    monkeypatch.setattr(verify_call, "_search_configured", lambda: True)
    monkeypatch.setattr(verify_call.CallRun, "_script", broken)
    assert verify_call.main() == 1
    assert "CalledProcessError" in capsys.readouterr().out
    assert voxd.scripts() == []


def test_a_non_zero_launch_exit_fails_the_run_even_with_a_good_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "abcd1234"), {}, {"abcd1234": "stopped"})
    voxd.launch_code = 1
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 1
    assert "FAIL clean end" in capsys.readouterr().out


def test_a_zero_launch_exit_is_a_clean_end() -> None:
    judged = verify_judge.Judge(
        _GOOD, "", search_key=True, trace=_TRACE, cleanup=_CLEAN, launch_exit=0
    ).verdicts()
    assert {v.status for v in judged} == {"PASS"}


def test_a_launch_exit_other_than_zero_fails_the_clean_end() -> None:
    judged = verify_judge.Judge(
        _GOOD, "", search_key=True, trace=_TRACE, cleanup=_CLEAN, launch_exit=1
    ).verdicts()
    verdict = next(v for v in judged if v.name == "clean end")
    assert verdict.status == "FAIL"
    assert "exit 1" in verdict.why


def test_every_problem_and_the_left_running_worker_share_one_message() -> None:
    both = verify_workers.Cleanup(
        ("abcd1234",), ("abcd1234",), ("vox call stop failed: x",)
    )
    status, why = _cleanup_verdict(both)
    assert status == "FAIL"
    assert "vox call stop failed: x" in why
    assert "left abcd1234 running" in why


def test_a_new_worker_that_already_ended_is_information_not_a_failure() -> None:
    ended = verify_workers.WorkerRow("w9", "exited", "do things")
    cleanup = verify_workers.Cleanup(("abcd1234",), (), (), (), (ended,))
    status, why = _cleanup_verdict(cleanup)
    assert status == "PASS"
    assert "w9" in why
    assert "already ended" in why


def _refused_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, code: int, live_trace: Path
) -> tuple[_Voxd, _Cleanup, str]:
    previous = _trace_file(tmp_path, "mine-previous")
    voxd = _Voxd(None, {"theirs": "ready"}, {"theirs": "ready"})
    voxd.pre_status = _report(previous)
    voxd.launch_code = code
    voxd.launch_out = ""
    voxd.status_out = _live(live_trace)
    cleanup, status = _place(tmp_path, monkeypatch, voxd)
    return voxd, cleanup, status


def test_a_launch_that_exits_one_without_a_report_adopts_no_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    voxd, cleanup, status = _refused_launch(
        tmp_path, monkeypatch, code=1, live_trace=_trace_file(other, "theirs")
    )
    assert voxd.call_stops() == []
    assert voxd.stops() == []
    assert status == ""
    assert cleanup.started == ()
    assert any("exited 1 without a report" in p for p in cleanup.problems)
    assert all("timed out" not in p for p in cleanup.problems)


def test_a_killed_launch_still_adopts_the_call_that_appeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    trace = _trace_file(mine, "new")
    voxd = _Voxd(trace, {}, {"new": "ready"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.post_statuses = [_live(trace), _live(trace), _report(trace)]
    cleanup, _ = _place(tmp_path, monkeypatch, voxd)
    assert len(voxd.call_stops()) == 1
    assert voxd.stops() == ["new"]
    assert cleanup.problems == ()


def test_a_call_that_ended_before_the_stop_is_not_a_failed_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _trace_file(tmp_path, "new")
    voxd = _Voxd(trace, {}, {"new": "stopped"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.stop_call_code = 2
    voxd.post_statuses = [_live(trace), _live(trace), _report(trace)]
    cleanup, _ = _place(tmp_path, monkeypatch, voxd)
    assert len(voxd.call_stops()) == 1
    assert all("vox call stop" not in p for p in cleanup.problems)


def test_a_call_that_already_ended_is_not_stopped_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _trace_file(tmp_path, "new")
    voxd = _Voxd(trace, {}, {"new": "stopped"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.post_statuses = [_report(trace)]
    _place(tmp_path, monkeypatch, voxd)
    assert voxd.call_stops() == []


def test_a_status_taken_over_by_another_call_is_said_plainly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    trace = _trace_file(mine, "new")
    voxd = _Voxd(trace, {}, {"new": "stopped"})
    voxd.launch_code = 124
    voxd.launch_out = ""
    voxd.post_statuses = [_live(trace), _live(trace), _report(_trace_file(other))]
    cleanup, _ = _place(tmp_path, monkeypatch, voxd)
    assert any("another call replaced the status" in p for p in cleanup.problems)
    assert all("never ended" not in p for p in cleanup.problems)


def test_trace_rows_that_are_not_objects_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "call.jsonl"
    path.write_text(
        '[1]\n"x"\n3\n{"type": "worker_started", "detail": {"worker_id": "w"}}\n'
    )
    rows = verify_call.load_trace(f"trace: {path}")
    assert rows == [{"type": "worker_started", "detail": {"worker_id": "w"}}]


def test_a_failure_after_the_call_was_placed_is_not_called_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "new"), {}, {"new": "stopped"})

    def broken(
        self: object, launch: object, before: str, *, killed: bool
    ) -> tuple[str, str, list[str]]:
        raise ValueError("boom")

    monkeypatch.setattr(verify_call.CallRun, "_attribute", broken)
    assert _run_main(monkeypatch, tmp_path, voxd, search=True) == 1
    out = capsys.readouterr().out
    assert "boom" in out
    assert "FAIL setup" not in out


def test_a_launch_that_never_returned_is_judged_as_such() -> None:
    judged = verify_judge.Judge(
        _GOOD, "", search_key=True, launch_exit=-1, trace=_TRACE, cleanup=_CLEAN
    ).verdicts()
    verdict = next(v for v in judged if v.name == "clean end")
    assert verdict.status == "FAIL"
    assert "did not return" in verdict.why
    assert "-1" not in verdict.why


def test_a_judge_cannot_be_built_without_a_launch_exit() -> None:
    with pytest.raises(TypeError, match="launch_exit"):
        verify_judge.Judge(_GOOD, "", search_key=True)
