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
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self, cast, final

from verify_judge import Judge, is_final
from verify_workers import Cleanup, WorkerFleet

__all__ = ["CallRun", "is_live", "load_trace", "main", "script_plan"]

_CALL_TIMEOUT_S: Final = 420
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


@final
class CallRun:
    """Generate the caller's audio, place the call, and always stop it."""

    __slots__ = ("_cleanup", "_directory", "_placed")
    _directory: Path
    _placed: bool  # True once this run started a call; only then may it stop one.
    _cleanup: Cleanup | None  # None until the call has been placed and swept.

    def __new__(cls, directory: Path) -> Self:
        """Work inside *directory*, which holds the generated audio and script."""
        self = super().__new__(cls)
        self._directory = directory
        self._placed = False
        self._cleanup = None
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
        """Return the final ``vox call status`` text and the call's own output."""
        script = self._script(search=search)
        self._placed = True
        status = ""
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
            self._cleanup = self._sweep(status or self.status())
        return status, done.stdout + done.stderr

    @staticmethod
    def _sweep(status: str) -> Cleanup:
        """Stop the workers the call's trace says it started, and no others."""
        try:
            rows = load_trace(status)
        except ValueError as exc:
            return Cleanup((), (), (f"the call trace was unreadable: {exc}",))
        if rows is None:
            msg = "no call trace, so the workers this call started are unknown"
            return Cleanup((), (), (msg,))
        details = [
            cast("dict[str, object]", row["detail"])
            for row in rows
            if row.get("type") == "worker_started"
            and isinstance(row.get("detail"), dict)
        ]
        ids = tuple(
            d["worker_id"] for d in details if isinstance(d.get("worker_id"), str)
        )
        return WorkerFleet(cast("tuple[str, ...]", ids)).sweep()

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
    trace = load_trace(report)
    verdicts = Judge(
        report, output, search_key=search, trace=trace, cleanup=run.cleanup
    ).verdicts()
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
