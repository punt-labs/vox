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

from verify_judge import Judge, is_final
from verify_workers import Cleanup, WorkerFleet

__all__ = [
    "CallRun",
    "is_live",
    "launch_timeout_s",
    "load_trace",
    "main",
    "script_plan",
]

_LAUNCH_MARGIN_S: Final = 240  # Speech, agent latency and retries on top of the pauses.
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
    return [json.loads(row) for row in text.splitlines() if row.strip()]


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
    """Return how long ``vox call`` may run: the script's own pauses plus a margin."""
    return sum(turn.pause_s for turn in script_plan(search=search)) + _LAUNCH_MARGIN_S


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
        finally:
            self._launched = launch
            mine, status, problems = self._attribute(launch, before)
            if failure:
                problems.insert(0, f"the call could not be run ({failure})")
            self._cleanup = self._sweep(fleet, mine, problems)
        output = "" if launch is None else launch.stdout + launch.stderr
        return status, output

    @property
    def launch_exit(self) -> int:
        """Return the exit status of ``vox call``, or -1 when it never returned."""
        return -1 if self._launched is None else self._launched.returncode

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
        self, launch: subprocess.CompletedProcess[str] | None, before: str
    ) -> tuple[str, str, list[str]]:
        """Return this run's call trace, its report and what could not be shown.

        A launch that printed its report named its call and has ended, so there
        is nothing to stop. Otherwise the call is found from the status and
        accepted only when its trace is new, and a live one is stopped.
        """
        if launch is not None and (mine := trace_path(launch.stdout)):
            return mine, self._settled_status(mine, launch.stdout), []
        how = "no result" if launch is None else f"exit {launch.returncode}"
        err = "" if launch is None else launch.stderr.strip()[:200]
        found = self.status() if self._placed else ""
        mine = trace_path(found)
        if not mine or mine == before:
            return (
                "",
                "",
                [f"the call did not start or could not be found ({how}: {err})"],
            )
        problems = [f"vox call timed out ({how}); its report was recovered from status"]
        if is_live(found):
            if fault := self._stop(mine):
                problems.append(fault)
        status = self._settled_status(mine, "")
        if not is_final(status):
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
        live status still names this run's call.
        """
        if not mine or trace_path(self.status()) != mine:
            return ""
        done = subprocess.run(
            ["timeout", str(_STOP_TIMEOUT_S), "vox", "call", "stop"],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            detail = done.stderr.strip()[:200] or "no stderr"
            return f"vox call stop failed (exit {done.returncode}: {detail})"
        return ""


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
            print(f"FAIL setup: {exc}")
            return 4
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
