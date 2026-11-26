"""Place one real scripted `vox call` and judge what the agent did.

Run through ``make verify-call``. It spends ElevenLabs credits and plays audio
on this machine, so it is never part of ``make check``. The call goes through
the installed voxd to the real ElevenLabs agent; the caller's turns are
generated here with macOS ``say`` and ``ffmpeg`` so no audio is committed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, final

__all__ = ["Judge", "Verdict"]

_CALL_TIMEOUT_S: Final = 300
_STOP_TIMEOUT_S: Final = 30
_KEYS_ENV: Final = Path.home() / ".punt-labs" / "vox" / "keys.env"
_OLD_OPENER: Final = re.compile(r"start,? steer,? check,? and stop coding", re.I)
_NAG: Final = re.compile(r"are you (still )?there|still there", re.I)
_MARKDOWN: Final = re.compile(r"(^|\s)#{1,6}\s|\*\*|`|^\s*[-*]\s", re.M)
_TURNS: Final = (
    # (text, seconds of silence after it)
    ("What does the pallets slash flask repository say about app factories?", 45),
    ("Search the web for the current weather in Tokyo.", 30),
    ("Give me a minute, I need to think.", 40),
)

type Status = Literal["PASS", "FAIL", "SKIP"]


@dataclass(frozen=True, slots=True)
class Verdict:
    """One expectation, its outcome and why."""

    name: str
    status: Status
    why: str


@final
class Judge:
    """Read the end-of-call report ``vox call`` prints and judge six expectations."""

    __slots__ = ("_agent", "_ended", "_output", "_report", "_search_key", "_tools")
    _report: str
    _output: str
    _search_key: bool
    _agent: list[str]
    _tools: list[str]
    _ended: bool

    def __new__(cls, report: str, output: str, *, search_key: bool) -> Self:
        """Bind the report, the call's other output and whether search is set up."""
        self = super().__new__(cls)
        self._report, self._output, self._search_key = report, output, search_key
        lines = report.splitlines()
        self._agent = [ln.split(": ", 1)[1] for ln in lines if ln.startswith("agent: ")]
        self._tools = [ln for ln in lines if ln.startswith("tool: ")]
        self._ended = any(ln.startswith("ended by ") for ln in lines)
        return self

    def verdicts(self) -> list[Verdict]:
        """Return the six expectations in order."""
        return [
            self._greeting(),
            self._docs(),
            self._plain_words(),
            self._search(),
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
        return Verdict("docs", "PASS", f"{len(calls)} read_docs call(s) ok")

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

    def _silence(self) -> Verdict:
        nags = [t for t in self._agent if _NAG.search(t)]
        if nags:
            return Verdict("silence", "FAIL", f"the agent nagged: {nags[0]!r}")
        return Verdict("silence", "PASS", "no 'are you there' in the whole call")

    def _clean_end(self) -> Verdict:
        if "RuntimeWarning" in self._output or "Traceback" in self._output:
            return Verdict("clean end", "FAIL", "warning or traceback on hang-up")
        if not self._ended:
            return Verdict("clean end", "FAIL", "the report names no end of the call")
        return Verdict("clean end", "PASS", "ended without a warning")


@final
class CallRun:
    """Generate the caller's audio, place the call, and always stop it."""

    __slots__ = ("_directory",)
    _directory: Path

    def __new__(cls, directory: Path) -> Self:
        """Work inside *directory*, which holds the generated audio and script."""
        self = super().__new__(cls)
        self._directory = directory
        return self

    def place(self, *, search: bool) -> tuple[str, str]:
        """Return the call's report and the rest of its output."""
        script = self._script(search=search)
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
        finally:
            self._stop()
        return done.stdout, done.stderr

    def _script(self, *, search: bool) -> Path:
        turns = [t for t in _TURNS if search or "weather" not in t[0]]
        rows = []
        for index, (text, pause) in enumerate(turns):
            pcm = self._pcm(index, text)
            rows.append(json.dumps({"pcm_path": pcm.name, "pause_s": pause}))
        script = self._directory / "script.jsonl"
        script.write_text("\n".join(rows) + "\n")
        return script

    def _pcm(self, index: int, text: str) -> Path:
        aiff = self._directory / f"turn{index}.aiff"
        pcm = self._directory / f"turn{index}.pcm"
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

    @staticmethod
    def _stop() -> None:
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
    search = _search_configured()
    with tempfile.TemporaryDirectory(prefix="verify-call-") as directory:
        report, output = CallRun(Path(directory)).place(search=search)
    verdicts = Judge(report, output, search_key=search).verdicts()
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
