"""Receive an isolated worker environment and exec its harness directly."""

from __future__ import annotations

import json
import os
import socket
import sys
from typing import cast, final

__all__ = ["WorkerBootstrap"]


@final
class WorkerBootstrap:
    """Run after env -i without ever putting credential values in argv."""

    __slots__ = ()

    @staticmethod
    def run(address: str, argv: list[str]) -> None:
        """Read the private broker, validate the environment, then replace self."""
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
        os.execvpe(argv[0], argv, cast("dict[str, str]", fields))


if __name__ == "__main__":
    try:
        WorkerBootstrap.run(sys.argv[1], sys.argv[2:])
    except (OSError, ValueError, IndexError):
        sys.stderr.write("Worker bootstrap failed\n")
        raise SystemExit(1) from None
