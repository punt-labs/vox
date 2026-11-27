"""Place one real scripted `vox call` and judge what the agent did.

Run through ``make verify-call``. It spends ElevenLabs credits and plays audio
on this machine, so it is never part of ``make check``. The call goes through
the installed voxd to the real ElevenLabs agent; the caller's turns are
generated here with macOS ``say`` and ``ffmpeg`` so no audio is committed.
The call also starts one read-only worker, asks what it is doing and stops it;
any worker the run started and the agent left behind is stopped afterwards.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, cast, final

__all__ = ["Judge", "Verdict", "is_final", "is_live", "load_trace", "script_plan"]

_CALL_TIMEOUT_S: Final = 420
_STOP_TIMEOUT_S: Final = 30
_KEYS_ENV: Final = Path.home() / ".punt-labs" / "vox" / "keys.env"
_OLD_OPENER: Final = re.compile(r"start,? steer,? check,? and stop coding", re.I)
_NAG: Final = re.compile(r"are you (still )?there|still there", re.I)
_MARKDOWN: Final = re.compile(r"(^|\s)#{1,6}\s|\*\*|`|^\s*[-*]\s", re.M)
_LEAD_IN_S: Final = 6  # Silence before the caller speaks, so the greeting is whole.
_DOCS_TURN: Final = (
    "What is the Python SDK repository for the Model Context Protocol? "
    "Look it up in the docs."
)
_SEARCH_TURN: Final = (
    "Please search the web for the latest open source language models."
)
_SILENCE_TURN: Final = "Give me a minute, I need to think."
_WORKER_START_TURN: Final = (
    "Please start a coding worker in this repository. Its task is to run "
    "git status and report the result. It must not change any file."
)
_WORKER_STATUS_TURN: Final = "Tell me what the worker is doing right now."
_WORKER_GO_TURN: Final = "Yes, go ahead."
_WORKER_STOP_TURN: Final = (
    "Please stop the git status worker you just started, not any older worker."
)
_WORKER_CONFIRM_TURN: Final = "Yes, stop it."
# The words the agent may speak for a worker; the launch phase is not one.
_WORKER_STATES: Final = ("working", "blocked", "idle", "finished")
_WORKER_ENDED: Final = ("stopped", "exited")  # Registry phases with nothing to stop.

_FINAL_STATES: Final = ("call ended", "call failed")
_SETTLE_POLLS: Final = 10
_SETTLE_WAIT_S: Final = 2.0

type Status = Literal["PASS", "FAIL", "SKIP"]


def is_live(status: str) -> bool:
    """Return whether ``vox call status`` text reports a call in progress."""
    first = status.splitlines()[0] if status.strip() else ""
    return first.startswith(("call live", "call connecting"))


def load_trace(status: str) -> list[dict[str, object]] | None:
    """Return the trace rows named by the status text's ``trace:`` line, if readable."""
    for line in status.splitlines():
        if line.startswith("trace: "):
            try:
                text = Path(line[len("trace: ") :]).read_text(encoding="utf-8")
            except OSError:
                return None
            return [json.loads(row) for row in text.splitlines() if row.strip()]
    return None


def is_final(status: str) -> bool:
    """Return whether ``vox call status`` text reports a call that has ended."""
    first = status.splitlines()[0] if status.strip() else ""
    return first.startswith(_FINAL_STATES)


@dataclass(frozen=True, slots=True)
class Turn:
    """One scripted caller turn: what is said ("" is silence) and the pause after."""

    text: str
    pause_s: int


def script_plan(*, search: bool) -> list[Turn]:
    """Return the caller's turns: lead-in, docs, search if set up, worker, silence."""
    plan = [Turn("", _LEAD_IN_S), Turn(_DOCS_TURN, 45)]
    if search:
        plan.append(Turn(_SEARCH_TURN, 30))
    plan += [
        Turn(_WORKER_START_TURN, 25),
        Turn(_WORKER_GO_TURN, 50),
        Turn(_WORKER_STATUS_TURN, 40),
        Turn(_WORKER_STOP_TURN, 30),
        Turn(_WORKER_CONFIRM_TURN, 25),
        Turn(_SILENCE_TURN, 40),
    ]
    return plan


@dataclass(frozen=True, slots=True)
class Verdict:
    """One expectation, its outcome and why."""

    name: str
    status: Status
    why: str


@final
class Judge:
    """Read the end-of-call report ``vox call`` prints and judge seven expectations."""

    __slots__ = (
        "_agent",
        "_ended",
        "_output",
        "_report",
        "_search_key",
        "_tools",
        "_trace",
    )
    _report: str
    _output: str
    _search_key: bool
    _agent: list[str]
    _tools: list[str]
    _ended: bool
    _trace: list[dict[str, object]] | None  # None: no trace was read.

    def __new__(
        cls,
        report: str,
        output: str,
        *,
        search_key: bool,
        trace: list[dict[str, object]] | None = None,
    ) -> Self:
        """Bind the report, the call's other output and whether search is set up."""
        self = super().__new__(cls)
        self._report, self._output, self._search_key = report, output, search_key
        self._trace = trace
        self._agent, self._tools, self._ended = self._parse(report)
        return self

    @staticmethod
    def _parse(report: str) -> tuple[list[str], list[str], bool]:
        """Return agent replies (continuation lines joined), tool lines, ended."""
        agent: list[str] = []
        tools: list[str] = []
        ended = False
        in_reply = False
        for line in report.splitlines():
            if line.startswith("agent: "):
                agent.append(line[len("agent: ") :])
                in_reply = True
            elif line.startswith(("user: ", "tool: ", "trace: ", "call ")):
                in_reply = False
                if line.startswith("tool: "):
                    tools.append(line)
            elif line.startswith("ended by "):
                in_reply, ended = False, True
            elif line.strip() and in_reply:
                agent[-1] += " " + line.strip()
        return agent, tools, ended

    def verdicts(self) -> list[Verdict]:
        """Return the seven expectations in order."""
        return [
            self._greeting(),
            self._docs(),
            self._plain_words(),
            self._search(),
            self._worker_status(),
            self._silence(),
            self._clean_end(),
        ]

    def _greeting(self) -> Verdict:
        first = self._agent[0] if self._agent else ""
        if not first:
            return Verdict("greeting", "FAIL", "the agent said nothing")
        if _OLD_OPENER.search(first):
            return Verdict("greeting", "FAIL", f"old workers opener: {first!r}")
        if "vox" not in first.lower():
            return Verdict("greeting", "FAIL", f"not the greeting: {first!r}")
        return Verdict("greeting", "PASS", first)

    def _docs(self) -> Verdict:
        calls = [t for t in self._tools if t.startswith("tool: read_docs(")]
        if not calls:
            return Verdict("docs", "FAIL", "read_docs was never called")
        if any(not c.endswith("-> ok") for c in calls):
            return Verdict("docs", "FAIL", f"read_docs errored: {calls}")
        if not self._replied_after("read_docs"):
            return Verdict(
                "docs", "FAIL", "no agent reply followed the read_docs result"
            )
        return Verdict(
            "docs", "PASS", f"{len(calls)} read_docs call(s) ok, then a reply"
        )

    def _replied_after(self, tool: str) -> bool:
        """Return whether an agent reply follows the tool's successful result."""
        if self._trace is None:
            return False
        results = [
            t for r in self._trace if (t := self._result_time(r, tool)) is not None
        ]
        if not results:
            return False
        first = min(results)
        replies = [
            float(r["ms"])
            for r in self._trace
            if r.get("type") == "agent_response"
            and isinstance(r.get("ms"), int | float)
        ]
        return any(ms > first for ms in replies)

    @staticmethod
    def _result_time(row: dict[str, object], tool: str) -> float | None:
        """Return the time of a successful result row for *tool*, else None."""
        detail = row.get("detail")
        ms = row.get("ms")
        if (
            row.get("type") != "agent_tool_response"
            or not isinstance(detail, dict)
            or detail.get("tool_name") != tool
            or detail.get("is_error")
            or not isinstance(ms, int | float)
        ):
            return None
        return float(ms)

    def _plain_words(self) -> Verdict:
        marked = [t for t in self._agent if _MARKDOWN.search(t)]
        if marked:
            return Verdict("plain words", "FAIL", f"markdown spoken: {marked[0]!r}")
        return Verdict("plain words", "PASS", "no markdown in any agent reply")

    def _search(self) -> Verdict:
        if not self._search_key:
            return Verdict("search", "SKIP", "search not configured (no EXA_API_KEY)")
        calls = [t for t in self._tools if t.startswith("tool: search_web(")]
        if not calls:
            return Verdict("search", "FAIL", "search_web was never called")
        if any(not c.endswith("-> ok") for c in calls):
            return Verdict("search", "FAIL", f"search_web errored: {calls}")
        return Verdict("search", "PASS", f"{len(calls)} search_web call(s) ok")

    def _worker_status(self) -> Verdict:
        name = "worker status"
        for tool in ("start_worker", "worker_status"):
            calls = [t for t in self._tools if t.startswith(f"tool: {tool}(")]
            if not calls:
                return Verdict(name, "FAIL", f"{tool} was never called")
            if any(not c.endswith("-> ok") for c in calls):
                return Verdict(name, "FAIL", f"{tool} errored: {calls}")
        if self._trace is None:
            return Verdict(name, "FAIL", "no trace to read the worker_status result")
        started = {
            cast("dict[str, object]", row["detail"]).get("worker_id")
            for row in self._trace
            if row.get("type") == "worker_started"
            and isinstance(row.get("detail"), dict)
        }
        if not started:
            return Verdict(name, "FAIL", "the trace shows no worker started")
        readings = [
            cast("dict[str, object]", row["detail"])
            for row in self._trace
            if row.get("type") == "worker_status_result"
            and isinstance(row.get("detail"), dict)
        ]
        if not readings:
            return Verdict(name, "FAIL", "the trace holds no worker_status result")
        for reading in readings:
            if reading.get("worker_id") not in started:
                return Verdict(
                    name,
                    "FAIL",
                    f"status is for a worker this call never started: {reading}",
                )
            if fault := self._reading_fault(reading):
                return Verdict(name, "FAIL", f"{fault}: {reading}")
        if not self._replied_after("worker_status"):
            return Verdict(name, "FAIL", "no agent reply followed worker_status")
        states = ", ".join(str(r["state"]) for r in readings)
        return Verdict(name, "PASS", f"worker_status returned {states} with activity")

    @staticmethod
    def _reading_fault(fields: dict[str, object]) -> str:
        """Return what is wrong with a traced worker_status result, else ''."""
        if fields.get("state") not in _WORKER_STATES:
            return "state is not working, blocked, idle or finished"
        lifecycle = fields.get("lifecycle")
        if not isinstance(lifecycle, str) or not lifecycle:
            return "no lifecycle"
        if not isinstance(fields.get("activity"), dict):
            return "activity is not an object"
        return ""

    def _silence(self) -> Verdict:
        nags = [t for t in self._agent if _NAG.search(t)]
        if nags:
            return Verdict("silence", "FAIL", f"the agent nagged: {nags[0]!r}")
        return Verdict("silence", "PASS", "no 'are you there' in the whole call")

    def _clean_end(self) -> Verdict:
        if not is_final(self._report):
            return Verdict("clean end", "FAIL", "the call was still live when judged")
        if "RuntimeWarning" in self._output or "Traceback" in self._output:
            return Verdict("clean end", "FAIL", "warning or traceback on hang-up")
        if not self._ended:
            return Verdict("clean end", "FAIL", "the report names no end of the call")
        return Verdict("clean end", "PASS", "ended without a warning")


@final
class CallRun:
    """Generate the caller's audio, place the call, and always stop it."""

    __slots__ = ("_directory", "_leftover", "_placed")
    _directory: Path
    _placed: bool  # True once this run started a call; only then may it stop one.
    _leftover: list[str]  # Workers this run's call started and the agent left running.

    def __new__(cls, directory: Path) -> Self:
        """Work inside *directory*, which holds the generated audio and script."""
        self = super().__new__(cls)
        self._directory = directory
        self._placed = False
        self._leftover = []
        return self

    @property
    def leftover_workers(self) -> list[str]:
        """Return the IDs of workers the call started that this run had to stop."""
        return list(self._leftover)

    def place(self, *, search: bool) -> tuple[str, str]:
        """Return the final ``vox call status`` text and the call's own output."""
        script = self._script(search=search)
        known = self._worker_ids()
        self._placed = True
        try:
            done = subprocess.run(
                [
                    "timeout",
                    str(_CALL_TIMEOUT_S),
                    "vox",
                    "call",
                    "--script",
                    str(script),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=_CALL_TIMEOUT_S + 15,
            )
            self._stop()
            status = self._settled_status()
        finally:
            self._stop()
            self._stop_new_workers(known)
        return status, done.stdout + done.stderr

    @staticmethod
    def _worker_ids() -> set[str]:
        """Return the IDs of workers still running.

        Raises:
            ValueError: If ``vox worker list`` printed something unreadable;
                guessing would risk stopping a worker this run did not start.
        """
        done = subprocess.run(
            ["timeout", str(_STOP_TIMEOUT_S), "vox", "worker", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            rows = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            msg = f"vox worker list was unreadable: {done.stdout!r}"
            raise ValueError(msg) from exc
        return {
            str(row["id"])
            for row in rows
            if isinstance(row, dict) and row.get("state") not in _WORKER_ENDED
        }

    def _stop_new_workers(self, known: set[str]) -> None:
        """Stop each worker that appeared during the call; never one already there."""
        for worker_id in sorted(self._worker_ids() - known):
            self._leftover.append(worker_id)
            subprocess.run(
                ["timeout", str(_STOP_TIMEOUT_S), "vox", "worker", "stop", worker_id],
                capture_output=True,
                check=False,
            )

    def _script(self, *, search: bool) -> Path:
        rows = []
        for index, turn in enumerate(script_plan(search=search)):
            pcm = self._pcm(index, turn.text, turn.pause_s)
            rows.append(
                json.dumps(
                    {
                        "pcm_path": pcm.name,
                        "pause_s": 0 if not turn.text else turn.pause_s,
                    }
                )
            )
        script = self._directory / "script.jsonl"
        script.write_text("\n".join(rows) + "\n")
        return script

    def _pcm(self, index: int, text: str, pause_s: int) -> Path:
        aiff = self._directory / f"turn{index}.aiff"
        pcm = self._directory / f"turn{index}.pcm"
        if not text:  # Silence: 16 kHz mono s16le zeros for the lead-in.
            pcm.write_bytes(bytes(16000 * 2 * pause_s))
            return pcm
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(aiff),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "s16le",
                str(pcm),
            ],
            check=True,
        )
        return pcm

    def _settled_status(self) -> str:
        """Poll briefly until the call reports an ended state; return that text."""
        status = self.status()
        for _ in range(_SETTLE_POLLS):
            if is_final(status):
                break
            time.sleep(_SETTLE_WAIT_S)
            status = self.status()
        return status

    @staticmethod
    def status() -> str:
        """Return the status text after the call, which is the full report."""
        done = subprocess.run(
            ["timeout", str(_STOP_TIMEOUT_S), "vox", "call", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.stdout

    def _stop(self) -> None:
        """Stop the call this run placed; never one it did not start."""
        if not self._placed:
            return
        subprocess.run(
            ["timeout", str(_STOP_TIMEOUT_S), "vox", "call", "stop"],
            capture_output=True,
            check=False,
        )


def _search_configured() -> bool:
    if os.environ.get("EXA_API_KEY", "").strip():
        return True
    try:
        text = _KEYS_ENV.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.strip().startswith("EXA_API_KEY=") and line.split("=", 1)[1].strip()
        for line in text.splitlines()
    )


def main() -> int:
    """Place the call, print each verdict, and exit non-zero on any FAIL."""
    missing = [
        tool for tool in ("say", "ffmpeg", "vox", "timeout") if not shutil.which(tool)
    ]
    if missing:
        print(f"FAIL setup: missing {', '.join(missing)}")
        return 2
    if is_live(CallRun.status()):
        print("FAIL setup: a call is already live; refusing to run or stop it")
        return 3
    search = _search_configured()
    with tempfile.TemporaryDirectory(prefix="verify-call-") as directory:
        run = CallRun(Path(directory))
        report, output = run.place(search=search)
    if run.leftover_workers:
        print(f"stopped a worker the call left running: {run.leftover_workers}")
    trace = load_trace(report)
    verdicts = Judge(report, output, search_key=search, trace=trace).verdicts()
    for verdict in verdicts:
        print(f"{verdict.status} {verdict.name}: {verdict.why}")
    failed = [v.name for v in verdicts if v.status == "FAIL"]
    if failed:
        print(f"verify-call FAILED: {', '.join(failed)}")
        print("--- call report ---")
        print(report)
        return 1
    skipped = [v.name for v in verdicts if v.status == "SKIP"]
    print(
        f"verify-call passed ({len(skipped)} skipped: {', '.join(skipped) or 'none'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
