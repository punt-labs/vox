"""Who a process is: its start time and command line, as ``ps`` reports them.

A pid alone names whichever process the kernel last gave that number to. The
pair (start time, command line) names one process: a reused pid has a later
start time, so a handle that re-reads the pair and finds it changed knows the
process it identified has exited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import ClassVar, Self, final

__all__ = ["ProcessIdentity"]


@final
@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """One process's start time and command line."""

    started: str
    command: str

    # ``lstart`` under LC_ALL=C is fixed width: "Tue Nov  4 15:53:01 2026".
    _LSTART_WIDTH: ClassVar[int] = 24
    # The program the unit and plist run: ServiceInstaller._voxd_exec_args
    # writes ``<dist>/bin/voxd --port 8421`` as ExecStart/ProgramArguments.
    # voxd is a console script, so the kernel shows it either as itself or as
    # its interpreter followed by the script path.
    _PROGRAM: ClassVar[str] = "voxd"
    _INTERPRETER: ClassVar[re.Pattern[str]] = re.compile(r"^python[0-9.]*$")

    @classmethod
    def from_ps(cls, line: str) -> Self:
        """Parse one ``ps -o lstart= -o command=`` line; raise if malformed."""
        started = line[: cls._LSTART_WIDTH].strip()
        command = line[cls._LSTART_WIDTH :].strip()
        if not started or not command:
            msg = f"unparseable ps line: {line!r}"
            raise ValueError(msg)
        return cls(started=started, command=command)

    def is_voxd(self) -> bool:
        """Return True when the program is voxd, not merely an argument.

        Accepts ``.../voxd ...`` and ``.../python[3.x] .../voxd ...``. A
        process that only passes ``voxd`` as an argument (``--name voxd``,
        ``python -m tool voxd``) is not voxd.
        """
        argv = self.command.split()
        program = PurePosixPath(argv[0]).name
        if program == self._PROGRAM:
            return True
        return (
            len(argv) > 1
            and bool(self._INTERPRETER.match(program.lower()))
            and PurePosixPath(argv[1]).name == self._PROGRAM
        )
