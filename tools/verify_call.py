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

from verify_judge import TIMEOUT_EXIT, Judge, is_final
from verify_script import PCM_BYTES_PER_S, launch_timeout_s, script_plan
from verify_workers import Cleanup, WorkerFleet

__all__ = ["CallRun", "Trace", "is_live", "load_trace", "main", "trace_path"]

_STOP_TIMEOUT_S: Final = 30
_KEYS_ENV: Final = Path.home() / ".punt-labs" / "vox" / "keys.env"
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


@dataclass(frozen=True, slots=True)
class Trace:
    """The object rows of a call trace, and how many other rows it held."""

    rows: list[dict[str, object]]
    dropped: int  # Rows that were valid JSON but not objects.


def load_trace(status: str) -> Trace | None:
    """Return the trace named by the status text's ``trace:`` line, if readable.

    Raises:
        ValueError: If a row is not JSON, as a half-written last line is not.
    """
    if not (path := trace_path(status)):
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    parsed = [json.loads(row) for row in text.splitlines() if row.strip()]
    rows = [row for row in parsed if isinstance(row, dict)]
    return Trace(rows, len(parsed) - len(rows))


@final
class CallRun:
    """Generate the caller's audio, place the call, and always stop it."""

    __slots__ = ("_audio_s", "_cleanup", "_directory", "_launched", "_placed")
    _audio_s: float  # How long the generated script plays, once it is built.
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
        self._audio_s = 0.0
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
            ValueError: If the workers or the call status cannot be read before
                anything is spent.
        """
        fleet = WorkerFleet.observe()
        try:
            before = trace_path(self.status())
        except ValueError as exc:
            raise ValueError(
                f"could not read the call status before launching: {exc}"
            ) from exc
        launch: subprocess.CompletedProcess[str] | None = None
        failure = ""
        killed = False
        try:
            script = self._script(search=search)
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
    def audio_s(self) -> float:
        """Return how long the generated script plays, 0.0 before it is built."""
        return self._audio_s

    @property
    def placed(self) -> bool:
        """Return whether a launch was attempted, so a failure is past setup."""
        return self._placed

    @property
    def launch_exit(self) -> int | None:  # None: the launch never returned.
        """Return the exit status of ``vox call``, or None when it never returned."""
        return None if self._launched is None else self._launched.returncode

    def _launch(
        self, script: Path, *, search: bool
    ) -> subprocess.CompletedProcess[str]:
        timeout = launch_timeout_s(search=search, audio_s=self._audio_s)
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
            problems: list[str] = []
            status = self._read_report(mine, launch.stdout, problems)
            return mine, status, problems
        if launch is not None and launch.returncode != TIMEOUT_EXIT:
            err = launch.stderr.strip()[:200]
            msg = f"vox call exited {launch.returncode} without a report ({err})"
            return "", "", [msg]  # Not killed: no call is this run's to adopt.
        if launch is None and not killed:
            return "", "", ["the launch never ran, so no call is this run's"]
        try:
            found = self.status()
        except ValueError as exc:
            return "", "", [f"could not read the call status to find the call: {exc}"]
        mine = trace_path(found)
        if not mine or mine == before:
            return "", "", ["the call timed out and could not be found from status"]
        problems = []  # Recovery is the normal end of a scripted call.
        if fault := self._stop(mine):
            problems.append(fault)
        return mine, self._read_report(mine, "", problems), problems

    def _read_report(self, mine: str, fallback: str, problems: list[str]) -> str:
        """Return the ended report of call ``mine``, noting in ``problems`` if not."""
        try:
            status = self._settled_status(mine, fallback)
        except ValueError as exc:
            problems.append(f"could not read the call status for the report: {exc}")
            return fallback
        if not status:
            problems.append("another call replaced the status before this report")
        elif is_live(status):
            problems.append("the call is still live; run `vox call stop`")
        elif not is_final(status):
            problems.append("the call never reached an ended status")
        return status

    def _sweep(self, fleet: WorkerFleet, mine: str, problems: list[str]) -> Cleanup:
        """Stop the workers the call's trace names, and name any others."""
        ids: tuple[str, ...] = ()
        if mine:
            try:
                loaded = load_trace(f"trace: {mine}")
            except ValueError as exc:
                problems.append(f"the call trace was unreadable: {exc}")
                loaded = Trace([], 0)
            if loaded is None:
                problems.append(
                    "the call trace could not be read, so its workers are unknown"
                )
                loaded = Trace([], 0)
            if loaded.dropped:
                problems.append(
                    f"the call trace held {loaded.dropped} row(s) that are not objects"
                )
            rows = loaded.rows
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
        audio_bytes = 0
        for index, turn in enumerate(script_plan(search=search)):
            pcm = self._pcm(index, turn.text, turn.pause_s)
            audio_bytes += pcm.stat().st_size
            rows.append(
                json.dumps(
                    {
                        "pcm_path": pcm.name,
                        "pause_s": 0 if not turn.text else turn.pause_s,
                    }
                )
            )
        self._audio_s = audio_bytes / PCM_BYTES_PER_S
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
        """Return the status text after the call, which is the full report.

        Raises:
            ValueError: If ``vox call status`` failed or timed out, so that a
                failed read is never mistaken for an idle daemon.
        """
        try:
            done = subprocess.run(
                ["timeout", str(_STOP_TIMEOUT_S), "vox", "call", "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=_STOP_TIMEOUT_S + 5,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ValueError(f"vox call status failed ({type(exc).__name__})") from exc
        if done.returncode != 0:
            detail = done.stderr.strip()[:200] or "no stderr"
            raise ValueError(
                f"vox call status failed (exit {done.returncode}: {detail})"
            )
        return done.stdout

    def _stop(self, mine: str) -> str:
        """Hang up the live call whose trace is ``mine``; return what went wrong.

        ``vox call stop`` stops whatever call is live, so it runs only while the
        live status still names this run's call. A stop that fails because the
        call ended in the meantime is not a failure.
        """
        try:
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
        except ValueError as exc:
            return (
                "could not read the call status to hang up; "
                f"run `vox call stop` ({exc})"
            )
        detail = done.stderr.strip()[:200] or "no stderr"
        return f"vox call stop failed (exit {done.returncode}: {detail})"

    def _is_mine_and_live(self, mine: str) -> bool:
        """Return whether the live call is the one with trace ``mine``.

        Raises:
            ValueError: If the status cannot be read.
        """
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
    try:
        live = is_live(CallRun.status())
    except ValueError as exc:
        print(f"FAIL setup: could not read the call status ({exc})")
        return 3
    if live:
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
    trace_problem = ""
    try:
        loaded = load_trace(report)
    except ValueError as exc:
        loaded = None
        trace_problem = f"trace unreadable ({exc})"
    trace = None if loaded is None else loaded.rows
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
    if trace_problem:
        print(f"FAIL run: {trace_problem}")
        failed.append("trace")
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
