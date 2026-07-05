"""The production player: play a Part's file as a reduced-volume subprocess.

Resolves a Part to its file within the active Program directory, builds the
platform player argv (``afplay`` on macOS, ``ffplay`` on Linux) at reduced
volume so speech and chimes overlay on top, and spawns it. The process handle
logs a non-zero exit so a failed player surfaces instead of vanishing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import platform
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.part import Part

__all__ = ["SubprocessPlayer"]

logger = logging.getLogger(__name__)

_MUSIC_VOLUME = 30


@final
class SubprocessHandle:
    """A live player subprocess: awaited to completion, killable, exit-logged."""

    __slots__ = ("_proc",)
    _proc: asyncio.subprocess.Process

    def __new__(cls, proc: asyncio.subprocess.Process) -> Self:
        self = super().__new__(cls)
        self._proc = proc
        return self

    async def wait(self) -> int:
        """Block until the player exits, logging a non-zero exit code."""
        rc = await self._proc.wait()
        if rc != 0:
            await self._log_exit(rc)
        return rc

    async def kill(self) -> None:
        """Stop the player now and reap it."""
        if self._proc.returncode is None:
            self._proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await self._proc.wait()

    async def _log_exit(self, rc: int) -> None:
        stderr_text = ""
        if self._proc.stderr is not None:
            stderr_bytes = await self._proc.stderr.read()
            stderr_text = stderr_bytes.decode(errors="replace").strip()
        logger.warning("player exited with rc=%s: %s", rc, stderr_text)


@final
class SubprocessPlayer:
    """Play Parts from one Program directory as reduced-volume subprocesses."""

    __slots__ = ("_directory",)
    _directory: Path

    def __new__(cls, directory: Path) -> Self:
        self = super().__new__(cls)
        self._directory = directory
        return self

    async def play(self, part: Part) -> SubprocessHandle:
        """Spawn the player for ``part`` and return its handle."""
        proc = await asyncio.create_subprocess_exec(
            *self._command(self._directory / part.identity),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        return SubprocessHandle(proc)

    @staticmethod
    def _command(path: Path) -> list[str]:
        """Return the reduced-volume player argv for ``path``."""
        if platform.system() == "Darwin":
            return ["afplay", "--volume", "0.3", str(path)]
        return [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-volume",
            str(_MUSIC_VOLUME),
            str(path),
        ]
