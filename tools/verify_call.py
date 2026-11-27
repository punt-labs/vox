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
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Self, cast, final

from verify_judge import NO_EXIT, TIMEOUT_EXIT, Judge, is_final
from verify_workers import Cleanup, WorkerFleet

__all__ = [
    "CallRun",
    "is_live",
    "launch_timeout_s",
    "load_trace",
    "main",
    "script_plan",
]

# ``vox call --script`` never returns on its own once the script ends: it idles
# on pings until ``timeout`` kills it, so exit 124 is the normal end of a call.
# The timeout is therefore the script itself plus a short drain, not a margin.
_SPEECH_ESTIMATE_S: Final = 3  # Per spoken turn, on top of its pause.
_DRAIN_S: Final = 45  # For the agent's last reply and any worker stop in flight.
_STOP_TIMEOUT_S: Final = 30
_KEYS_ENV: Final = Path.home() / ".punt-labs" / "vox" / "keys.env"
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
_WORKER_GO_TURN: Final = (
    "Yes."  # The gate accepts only a bare yes from its closed list.
)
_WORKER_STOP_TURN: Final = (
    "Please stop the git status worker you just started, not any older worker."
)
_WORKER_CONFIRM_TURN: Final = "Yes."

_SETTLE_POLLS: Final = 10
_SETTLE_WAIT_S: Final = 2.0


def is_live(status: str) -> bool:
    """Return whether ``vox call status`` text reports a call in progress."""
    first = status.splitlines()[0] if status.strip() else ""
    return first.startswith(("call live", "call connecting"))


def trace_path(status: str) -> str:
    """Return the file named by the text's ``trace:`` line, or '' if it has none."""
    for line in status.splitlines():
        if line.startswith("trace: "):
            return line[len("trace: ") :]
    return ""


def load_trace(status: str) -> list[dict[str, object]] | None:
    """Return the trace rows named by the status text's ``trace:`` line, if readable."""
    if not (path := trace_path(status)):
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    parsed = [json.loads(row) for row in text.splitlines() if row.strip()]
    return [row for row in parsed if isinstance(row, dict)]


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


def launch_timeout_s(*, search: bool) -> int:
    """Return when ``vox call`` is cut off: the script's length plus a short drain."""
    plan = script_plan(search=search)
    spoken = sum(1 for turn in plan if turn.text)
    return sum(t.pause_s for t in plan) + _SPEECH_ESTIMATE_S * spoken + _DRAIN_S


@final
class CallRun:
    """Generate the caller's audio, place the call, and always stop it."""

    __slots__ = ("_cleanup", "_directory", "_launched", "_placed")
    _directory: Path
    _placed: bool  # True once this run started a call; only then may it stop one.
    _cleanup: Cleanup | None  # None until the call has been placed and swept.
    _launched: subprocess.CompletedProcess[str] | None  # None: it never returned.

    def __new__(cls, directory: Path) -> Self:
        """Work inside *directory*, which holds the generated audio and script."""
        self = super().__new__(cls)
        self._directory = directory
        self._placed = False
        self._cleanup = None
        self._launched = None
        return self

    @property
    def cleanup(self) -> Cleanup:
        """Return what became of the workers the call started.

        Raises:
            ValueError: If no call has been placed and swept yet.
        """
        if self._cleanup is None:
            raise ValueError("no call has been placed")
        return self._cleanup

    def place(self, *, search: bool) -> tuple[str, str]:
        """Return the report of the call this run placed and the launch's output.

        The call's own trace says which workers it started. A launch that
        prints no trace line (killed by its timeout) is recovered from the
        status, which counts only when its trace differs from the one before
        the launch. The report is empty when no call can be attributed.

        Raises:
            ValueError: If the workers cannot be listed before anything is spent.
        """
        fleet = WorkerFleet.observe()
        launch: subprocess.CompletedProcess[str] | None = None
        failure = ""
        before = ""
        killed = False
        try:
            script = self._script(search=search)
            before = trace_path(self.status())
            launch = self._launch(script, search=search)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            failure = f"{type(exc).__name__}: {str(exc)[:200]}"
            killed = isinstance(exc, subprocess.TimeoutExpired)
        finally:
            self._launched = launch
            mine, status, problems = self._attribute(launch, before, killed=killed)
            if failure:
                problems.insert(0, f"the call could not be run ({failure})")
            self._cleanup = self._sweep(fleet, mine, problems)
        output = "" if launch is None else launch.stdout + launch.stderr
        return status, output

    @property
    def placed(self) -> bool:
        """Return whether a launch was attempted, so a failure is past setup."""
        return self._placed

    @property
    def launch_exit(self) -> int:
        """Return the exit status of ``vox call``, or NO_EXIT when it never returned."""
        return NO_EXIT if self._launched is None else self._launched.returncode

    def _launch(
        self, script: Path, *, search: bool
    ) -> subprocess.CompletedProcess[str]:
        timeout = launch_timeout_s(search=search)
        self._placed = True
        return subprocess.run(
            ["timeout", str(timeout), "vox", "call", "--script", str(script)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout + 15,
        )

    def _attribute(
        self,
        launch: subprocess.CompletedProcess[str] | None,
        before: str,
        *,
        killed: bool,
    ) -> tuple[str, str, list[str]]:
        """Return this run's call trace, its report and what could not be shown.

        A launch that printed its report named its call and has ended, so there
        is nothing to stop. A scripted call normally ends by ``timeout`` killing
        the launch (exit 124): its call is then found from the status, accepted
        only when its trace is new, and a live one is hung up.
        """
        if launch is not None and (mine := trace_path(launch.stdout)):
            return mine, self._settled_status(mine, launch.stdout), []
        if launch is not None and launch.returncode != TIMEOUT_EXIT:
            err = launch.stderr.strip()[:200]
            msg = f"vox call exited {launch.returncode} without a report ({err})"
            return "", "", [msg]  # Not killed: no call is this run's to adopt.
        if launch is None and not killed:
            return "", "", ["the launch never ran, so no call is this run's"]
        found = self.status()
        mine = trace_path(found)
        if not mine or mine == before:
            return "", "", ["the call timed out and could not be found from status"]
        problems: list[str] = []  # Recovery is the normal end of a scripted call.
        if fault := self._stop(mine):
            problems.append(fault)
        status = self._settled_status(mine, "")
        if not status:
            problems.append("another call replaced the status before this report")
        elif not is_final(status):
            problems.append("the call never ended after the hang-up")
        return mine, status, problems

    def _sweep(self, fleet: WorkerFleet, mine: str, problems: list[str]) -> Cleanup:
        """Stop the workers the call's trace names, and name any others."""
        ids: tuple[str, ...] = ()
        if mine:
            try:
                rows = load_trace(f"trace: {mine}")
            except ValueError as exc:
                problems.append(f"the call trace was unreadable: {exc}")
                rows = []
            if rows is None:
                problems.append(
                    "the call trace could not be read, so its workers are unknown"
                )
                rows = []
            details = [
                cast("dict[str, object]", row["detail"])
                for row in rows
                if row.get("type") == "worker_started"
                and isinstance(row.get("detail"), dict)
            ]
            ids = tuple(
                cast("str", d["worker_id"])
                for d in details
                if isinstance(d.get("worker_id"), str)
            )
        found = fleet.sweep(ids)
        return replace(found, problems=(*problems, *found.problems))

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

    def _settled_status(self, mine: str, fallback: str) -> str:
        """Return the ended report of the call whose trace is ``mine``.

        A status naming a different trace belongs to another call, and then
        ``fallback`` (the launch's own report, or '') stands in.
        """
        status = self.status()
        for _ in range(_SETTLE_POLLS):
            theirs = trace_path(status)
            if theirs and theirs != mine:
                return fallback
            if is_final(status) and theirs == mine:
                return status
            time.sleep(_SETTLE_WAIT_S)
            status = self.status()
        return status if trace_path(status) == mine else fallback

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

    def _stop(self, mine: str) -> str:
        """Hang up the live call whose trace is ``mine``; return what went wrong.

        ``vox call stop`` stops whatever call is live, so it runs only while the
        live status still names this run's call. A stop that fails because the
        call ended in the meantime is not a failure.
        """
        if not self._is_mine_and_live(mine):
            return ""
        done = subprocess.run(
            ["timeout", str(_STOP_TIMEOUT_S), "vox", "call", "stop"],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode == 0 or not self._is_mine_and_live(mine):
            return ""
        detail = done.stderr.strip()[:200] or "no stderr"
        return f"vox call stop failed (exit {done.returncode}: {detail})"

    def _is_mine_and_live(self, mine: str) -> bool:
        """Return whether the live call is the one with trace ``mine``."""
        if not mine:
            return False
        status = self.status()
        return trace_path(status) == mine and is_live(status)


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
        try:
            report, output = run.place(search=search)
        except ValueError as exc:
            if not run.placed:
                print(f"FAIL setup: {exc}")
                return 4
            print(f"FAIL run: {exc}")
            return 1
    trace = load_trace(report)
    verdicts = Judge(
        report,
        output,
        search_key=search,
        trace=trace,
        cleanup=run.cleanup,
        launch_exit=run.launch_exit,
    ).verdicts()
    for verdict in verdicts:
        print(f"{verdict.status} {verdict.name}: {verdict.why}")
    failed = [v.name for v in verdicts if v.status == "FAIL"]
    if failed:
        print(f"verify-call FAILED: {', '.join(failed)}")
        print("--- call report ---")
        print(report)
        print("--- launch output ---")
        print(output)
        return 1
    skipped = [v.name for v in verdicts if v.status == "SKIP"]
    print(
        f"verify-call passed ({len(skipped)} skipped: {', '.join(skipped) or 'none'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
