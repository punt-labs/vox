"""The verify-call judge reads a call's report and names each failed expectation."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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
    judged = verify_call.Judge(
        report, output, search_key=search_key, trace=_TRACE, cleanup=_CLEAN
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
        verify_call.Judge(_STATUS, "", search_key=True).verdicts()[0].status == "PASS"
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
    assert not verify_call.is_final(_LIVE)


def test_an_ended_status_is_final() -> None:
    assert verify_call.is_final(_STATUS)


def test_a_failed_status_is_final() -> None:
    assert verify_call.is_final("call failed | 0 tool calls\nerror: boom\n")


@pytest.mark.parametrize("text", ["", "call idle", "call connecting | 0 tool calls"])
def test_empty_idle_or_connecting_status_is_not_final(text: str) -> None:
    assert not verify_call.is_final(text)


def test_a_live_status_fails_the_clean_end_even_if_it_looks_complete() -> None:
    verdicts = {
        v.name: v for v in verify_call.Judge(_LIVE, "", search_key=True).verdicts()
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
]


def _with_trace(trace: list[dict[str, object]] | None) -> dict[str, str]:
    judged = verify_call.Judge(
        _GOOD, "", search_key=True, trace=trace, cleanup=_CLEAN
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
    verify_call.CallRun(tmp_path)._stop()
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
    judged = verify_call.Judge(
        report, "", search_key=True, trace=trace, cleanup=_CLEAN
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
    judged = verify_call.Judge(
        report, "", search_key=True, trace=_TRACE, cleanup=cleanup
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


class _Voxd:
    """Stand in for subprocess.run around one placed call."""

    def __init__(self, trace: Path | None, workers: dict[str, str]) -> None:
        self.trace = trace
        self.workers = workers
        self.list_code = 0
        self.commands: list[list[str]] = []

    def __call__(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[-2:] == ["call", "status"]:
            trace = f"trace: {self.trace}\n" if self.trace else ""
            return subprocess.CompletedProcess(
                command, 0, _GOOD.replace("trace: /tmp/call.jsonl\n", trace), ""
            )
        if command[-2:] == ["worker", "list"]:
            rows = [{"id": i, "state": s} for i, s in self.workers.items()]
            out = json.dumps(rows) if self.list_code == 0 else ""
            return subprocess.CompletedProcess(command, self.list_code, out, "no voxd")
        if command[3:5] == ["worker", "stop"]:
            self.workers[command[-1]] = "stopped"
        return subprocess.CompletedProcess(command, 0, "", "")

    def stops(self) -> list[str]:
        return [c[-1] for c in self.commands if c[3:5] == ["worker", "stop"]]


def _trace_file(directory: Path, *worker_ids: str) -> Path:
    path = directory / "call.jsonl"
    rows = [
        {"ms": 1, "dir": "note", "type": "worker_started", "detail": {"worker_id": i}}
        for i in worker_ids
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_a_call_stops_only_the_workers_its_trace_says_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "new"), {"old": "ready", "new": "ready"})
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    _stub_script(monkeypatch, tmp_path)
    run = verify_call.CallRun(tmp_path)
    run.place(search=False)
    assert voxd.stops() == ["new"]
    assert run.cleanup.left_running == ("new",)


def test_a_new_worker_the_trace_does_not_name_is_never_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(_trace_file(tmp_path), {"someone-elses": "ready"})
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    _stub_script(monkeypatch, tmp_path)
    verify_call.CallRun(tmp_path).place(search=False)
    assert voxd.stops() == []


def test_a_call_with_no_trace_cannot_name_its_workers_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voxd = _Voxd(None, {"old": "ready"})
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    _stub_script(monkeypatch, tmp_path)
    run = verify_call.CallRun(tmp_path)
    run.place(search=False)
    assert voxd.stops() == []
    assert "no call trace" in run.cleanup.problems[0]


def test_a_worker_list_failing_after_the_call_does_not_hide_the_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "abcd1234"), {})
    voxd.list_code = 1
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    monkeypatch.setattr(verify_call.shutil, "which", _which)
    monkeypatch.setattr(verify_call, "_search_configured", lambda: False)
    _stub_script(monkeypatch, tmp_path)
    _stub_trace(monkeypatch)
    assert verify_call.main() == 1
    out = capsys.readouterr().out
    assert "PASS greeting" in out
    assert "FAIL worker cleanup" in out
    assert "no voxd" in out


def test_a_clean_run_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "abcd1234"), {"abcd1234": "stopped"})
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    monkeypatch.setattr(verify_call.shutil, "which", _which)
    monkeypatch.setattr(verify_call, "_search_configured", lambda: True)
    _stub_script(monkeypatch, tmp_path)
    _stub_trace(monkeypatch)
    assert verify_call.main() == 0
    assert "PASS worker cleanup" in capsys.readouterr().out


def test_a_leftover_worker_makes_the_run_exit_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    voxd = _Voxd(_trace_file(tmp_path, "abcd1234"), {"abcd1234": "ready"})
    monkeypatch.setattr(verify_call.subprocess, "run", voxd)
    monkeypatch.setattr(verify_workers.subprocess, "run", voxd)
    monkeypatch.setattr(verify_call.shutil, "which", _which)
    monkeypatch.setattr(verify_call, "_search_configured", lambda: True)
    _stub_script(monkeypatch, tmp_path)
    _stub_trace(monkeypatch)
    assert verify_call.main() == 1
    assert "left" in capsys.readouterr().out
