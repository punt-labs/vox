"""Receive an isolated environment and supervise one harness process group."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from typing import TYPE_CHECKING, cast, final

if TYPE_CHECKING:
    from types import FrameType

__all__ = ["WorkerBootstrap"]


@final
class WorkerBootstrap:
    """Run after env -i without ever putting credential values in argv."""

    __slots__ = ()

    @staticmethod
    def run(address: str, argv: list[str]) -> None:
        """Read the private broker, validate the environment, and run the harness."""
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(15)
            connection.connect(address)
            with connection.makefile("r") as source:
                data = json.load(source)
        if not isinstance(data, dict):
            raise ValueError("Worker environment must be an object")
        fields = cast("dict[object, object]", data)
        if not all(
            isinstance(k, str) and isinstance(v, str) for k, v in fields.items()
        ):
            raise ValueError("Worker environment must contain strings")
        WorkerBootstrap._supervise(argv, cast("dict[str, str]", fields))

    @staticmethod
    def _hangup(
        _signal: int,
        _frame: FrameType | None,  # PY-TS-14: callbacks can lack a Python frame.
    ) -> None:
        """Terminate the group while its owning supervisor is still alive."""
        os.killpg(os.getpgrp(), signal.SIGKILL)

    @staticmethod
    def _supervise(argv: list[str], environment: dict[str, str]) -> None:
        group = os.getpgrp()
        if group != os.getpid():
            raise RuntimeError("Worker bootstrap must own its process group")
        previous = signal.signal(signal.SIGHUP, WorkerBootstrap._hangup)
        try:
            child = subprocess.Popen(argv, env=environment)
            while True:
                try:
                    child.wait()
                    break
                except KeyboardInterrupt:
                    continue
        finally:
            os.killpg(group, signal.SIGKILL)
            signal.signal(signal.SIGHUP, previous)


if __name__ == "__main__":
    try:
        WorkerBootstrap.run(sys.argv[1], sys.argv[2:])
    except (OSError, ValueError, IndexError, RuntimeError):
        sys.stderr.write("Worker bootstrap failed\n")
        raise SystemExit(1) from None
