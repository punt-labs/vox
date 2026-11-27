"""The verify-call judge reads a call's report and names each failed expectation."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "verify_call.py"
_SPEC = importlib.util.spec_from_file_location("verify_call", _PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
verify_call = importlib.util.module_from_spec(_SPEC)
sys.modules["verify_call"] = verify_call
_SPEC.loader.exec_module(verify_call)

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
        report, output, search_key=search_key, trace=_TRACE
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


def test_the_real_status_text_passes_every_expectation_but_the_worker_one() -> None:
    # The captured call predates the worker turns, so it holds no worker call.
    verdicts = _verdicts(_STATUS)
    assert verdicts.pop("worker status") == "FAIL"
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
    judged = verify_call.Judge(_GOOD, "", search_key=True, trace=trace).verdicts()
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
    judged = verify_call.Judge(report, "", search_key=True, trace=trace).verdicts()
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
    assert texts[start + 1] == "Yes, go ahead."
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
    assert texts[stop + 1].lower().startswith("yes")


def test_the_worker_task_is_read_only() -> None:
    texts = " ".join(t.text for t in verify_call.script_plan(search=False))
    assert "must not change any file" in texts


def _stub_script(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Replace the audio-generating script step with an empty script path."""

    def script(self: object, *, search: bool) -> Path:
        return directory / "script.jsonl"

    monkeypatch.setattr(verify_call.CallRun, "_script", script)


class _Fleet:
    """Stand in for subprocess.run with a worker list that grows once."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.listed = 0

    def __call__(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[-2:] == ["worker", "list"]:
            self.listed += 1
            ids = ["old"] if self.listed == 1 else ["old", "new"]
            return subprocess.CompletedProcess(
                command, 0, json.dumps([{"id": i} for i in ids]), ""
            )
        out = _ENDED_STATUS if command[-2:] == ["call", "status"] else ""
        return subprocess.CompletedProcess(command, 0, out, "")


_ENDED_STATUS = "call ended by caller | 0 tool calls\nended by caller\n"


def test_a_call_stops_a_worker_it_started_and_never_one_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = _Fleet()
    run = verify_call.CallRun(tmp_path)
    monkeypatch.setattr(verify_call.subprocess, "run", fleet)
    _stub_script(monkeypatch, tmp_path)
    run.place(search=False)
    stops = [c for c in fleet.commands if c[-3:-1] == ["worker", "stop"]]
    assert [c[-1] for c in stops] == ["new"]
    assert run.leftover_workers == ["new"]


def test_a_worker_the_agent_already_stopped_is_not_stopped_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Stopped(_Fleet):
        def __call__(
            self, command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            done = super().__call__(command, **kwargs)
            if command[-2:] != ["worker", "list"]:
                return done
            rows = json.loads(done.stdout)
            stopped = [
                {**r, "state": "stopped"} if r["id"] == "new" else r for r in rows
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(stopped), "")

    fleet = _Stopped()
    monkeypatch.setattr(verify_call.subprocess, "run", fleet)
    _stub_script(monkeypatch, tmp_path)
    run = verify_call.CallRun(tmp_path)
    run.place(search=False)
    assert run.leftover_workers == []


def test_an_unreadable_worker_list_refuses_to_place_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Garbled(_Fleet):
        def __call__(
            self, command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            super().__call__(command, **kwargs)
            out = "not json" if command[-2:] == ["worker", "list"] else ""
            return subprocess.CompletedProcess(command, 0, out, "")

    fleet = _Garbled()
    monkeypatch.setattr(verify_call.subprocess, "run", fleet)
    _stub_script(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="unreadable"):
        verify_call.CallRun(tmp_path).place(search=False)
    assert not any("--script" in c for c in fleet.commands)
    assert not any(c[-2:-1] == ["stop"] for c in fleet.commands)


def test_worker_commands_run_under_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = _Fleet()
    run = verify_call.CallRun(tmp_path)
    monkeypatch.setattr(verify_call.subprocess, "run", fleet)
    _stub_script(monkeypatch, tmp_path)
    run.place(search=False)
    worker = [c for c in fleet.commands if "worker" in c]
    assert worker
    assert all(c[0] == "timeout" for c in worker)
