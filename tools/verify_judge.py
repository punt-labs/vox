"""Judge the end-of-call report ``vox call`` prints against the expectations.

``tools/verify_call.py`` places the call; this module reads what came back and
names each expectation as PASS, FAIL or SKIP, with the reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Self, cast, final

if TYPE_CHECKING:
    from verify_workers import Cleanup

__all__ = ["Judge", "Verdict", "is_final"]

_OLD_OPENER: Final = re.compile(r"start,? steer,? check,? and stop coding", re.I)
_NAG: Final = re.compile(r"are you (still )?there|still there", re.I)
_MARKDOWN: Final = re.compile(r"(^|\s)#{1,6}\s|\*\*|`|^\s*[-*]\s", re.M)
# The words the agent may speak for a worker; the launch phase is not one.
_WORKER_STATES: Final = ("working", "blocked", "idle", "finished")
_FINAL_STATES: Final = ("call ended", "call failed")

type Status = Literal["PASS", "FAIL", "SKIP"]


def is_final(status: str) -> bool:
    """Return whether ``vox call status`` text reports a call that has ended."""
    first = status.splitlines()[0] if status.strip() else ""
    return first.startswith(_FINAL_STATES)


@dataclass(frozen=True, slots=True)
class Verdict:
    """One expectation, its outcome and why."""

    name: str
    status: Status
    why: str


@final
class Judge:
    """Read the end-of-call report ``vox call`` prints and judge eight expectations."""

    __slots__ = (
        "_agent",
        "_cleanup",
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
    _cleanup: Cleanup | None  # None: no worker cleanup was attempted.

    def __new__(
        cls,
        report: str,
        output: str,
        *,
        search_key: bool,
        trace: list[dict[str, object]] | None = None,
        cleanup: Cleanup | None = None,
    ) -> Self:
        """Bind the report, the call's other output and whether search is set up."""
        self = super().__new__(cls)
        self._report, self._output, self._search_key = report, output, search_key
        self._trace, self._cleanup = trace, cleanup
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
        """Return the eight expectations in order."""
        return [
            self._greeting(),
            self._docs(),
            self._plain_words(),
            self._search(),
            self._worker_status(),
            self._worker_cleanup(),
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

    def _replied_after(self, tool: str, *, within_turn: bool = False) -> bool:
        """Return whether an agent reply follows the tool's successful result.

        With ``within_turn`` the reply must come before the caller's next turn,
        so a later answer to some other request is not mistaken for this one.
        """
        if self._trace is None:
            return False
        results = [
            t for r in self._trace if (t := self._result_time(r, tool)) is not None
        ]
        if not results:
            return False
        first = min(results)
        limit = self._next_caller_turn(first) if within_turn else float("inf")
        return any(first < ms < limit for ms in self._times("agent_response"))

    def _next_caller_turn(self, after: float) -> float:
        """Return when the caller next spoke after ``after``; infinity if never."""
        return min(
            (ms for ms in self._times("user_transcript") if ms > after),
            default=float("inf"),
        )

    def _times(self, kind: str) -> list[float]:
        """Return the trace time of every row of one type."""
        return [
            float(r["ms"])
            for r in self._trace or []
            if r.get("type") == kind and isinstance(r.get("ms"), int | float)
        ]

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
        if not self._replied_after("worker_status", within_turn=True):
            return Verdict(
                name,
                "FAIL",
                "no agent reply followed worker_status before the next turn",
            )
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
        activity = fields.get("activity")
        if not isinstance(activity, dict):
            return "activity is not an object"
        files = cast("dict[str, object]", activity).get("files")
        if not isinstance(files, list) or not all(
            isinstance(f, str) for f in cast("list[object]", files)
        ):
            return "activity has no files list"
        return ""

    def _worker_cleanup(self) -> Verdict:
        name = "worker cleanup"
        cleanup = self._cleanup
        if cleanup is None:
            return Verdict(name, "FAIL", "no worker cleanup was attempted")
        found = [*cleanup.problems, *(row.describe() for row in cleanup.unproven)]
        if found:
            return Verdict(name, "FAIL", "; ".join(found))
        if not cleanup.started:
            return Verdict(name, "FAIL", "the call started no worker to stop")
        if cleanup.left_running:
            return Verdict(
                name,
                "FAIL",
                f"the agent left {', '.join(cleanup.left_running)} running; "
                "this run stopped it",
            )
        stops = [t for t in self._tools if t.startswith("tool: stop_worker(")]
        if not any(t.endswith("-> ok") for t in stops):
            return Verdict(name, "FAIL", f"stop_worker never succeeded: {stops}")
        return Verdict(name, "PASS", "stop_worker ok; no started worker still running")

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
