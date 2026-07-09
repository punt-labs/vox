"""Tests for the production subprocess player.

The player boundary is exercised with *real* trivial subprocesses (``true`` for a
clean exit, ``sh -c 'exit 3'`` for a non-zero exit, ``sleep`` for kill), so the
exit-logging and kill branches are covered against the real
``asyncio.create_subprocess_exec`` boundary rather than a mock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.voxd.programs import Part
from punt_vox.voxd.programs.subprocess_player import SubprocessHandle, SubprocessPlayer

if TYPE_CHECKING:
    import pytest


class FixedDirectory:
    """A mutable ``PlayerDirectory`` test double, resolved live on each spawn."""

    __slots__ = ("_directory",)

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def switch(self, directory: Path) -> None:
        self._directory = directory

    def locate(self, part: Part) -> Path:
        return self._directory / part.identity


async def _spawn(*argv: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )


class TestSubprocessHandle:
    async def test_clean_exit_returns_zero(self) -> None:
        handle = SubprocessHandle(await _spawn("true"))
        assert await handle.wait() == 0

    async def test_nonzero_exit_is_logged(self) -> None:
        handle = SubprocessHandle(await _spawn("sh", "-c", "echo boom >&2; exit 3"))
        assert await handle.wait() == 3  # the _log_exit error branch runs

    async def test_kill_stops_a_running_player(self) -> None:
        handle = SubprocessHandle(await _spawn("sleep", "5"))
        await handle.kill()
        assert await handle.wait() != 0  # reaped after the kill

    async def test_kill_tolerates_a_process_that_vanished_mid_kill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A natural exit can race the kill: the process is gone by the time
        # kill() fires, so kill() raises ProcessLookupError. The suppress must
        # now wrap kill() itself, so this is a safe no-op rather than a crash
        # that would propagate through the loop and silently stop playback.
        proc = await _spawn("sleep", "5")
        real_kill = proc.kill

        def _kill_then_vanish() -> None:
            real_kill()  # actually stop the process so wait() can reap it
            raise ProcessLookupError  # ...but report it as already gone

        monkeypatch.setattr(proc, "kill", _kill_then_vanish)
        handle = SubprocessHandle(proc)
        await handle.kill()  # must not raise
        assert proc.returncode is not None  # reaped despite the racing kill


class TestSubprocessPlayer:
    def test_darwin_command_uses_afplay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "punt_vox.voxd.programs.subprocess_player.platform.system",
            lambda: "Darwin",
        )
        argv = SubprocessPlayer._command(Path("/m/001.mp3"))
        assert argv[0] == "afplay"
        assert argv[-1] == "/m/001.mp3"

    def test_linux_command_uses_ffplay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "punt_vox.voxd.programs.subprocess_player.platform.system",
            lambda: "Linux",
        )
        argv = SubprocessPlayer._command(Path("/m/001.mp3"))
        assert argv[0] == "ffplay"
        assert argv[-1] == "/m/001.mp3"

    async def test_play_spawns_the_resolved_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen: list[str] = []

        async def _fake_exec(
            *argv: str, **_kwargs: object
        ) -> asyncio.subprocess.Process:
            seen.extend(argv)
            # A real, harmless process, spawned without re-entering the patch.
            return await asyncio.create_subprocess_shell(
                "true", stderr=asyncio.subprocess.PIPE
            )

        monkeypatch.setattr(
            "punt_vox.voxd.programs.subprocess_player.asyncio.create_subprocess_exec",
            _fake_exec,
        )
        player = SubprocessPlayer(FixedDirectory(tmp_path))
        handle = await player.play(Part("001.mp3", 1))
        assert isinstance(handle, SubprocessHandle)
        assert str(tmp_path / "001.mp3") in seen  # the resolved file was played
        await handle.wait()

    async def test_play_resolves_the_live_directory_each_spawn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The directory is read on every spawn, so a mid-flight switch lands the
        # player in the new pool -- the dynamic-player half of vox-73m5.
        seen: list[str] = []

        async def _fake_exec(
            *argv: str, **_kwargs: object
        ) -> asyncio.subprocess.Process:
            seen.extend(argv)
            return await asyncio.create_subprocess_shell(
                "true", stderr=asyncio.subprocess.PIPE
            )

        monkeypatch.setattr(
            "punt_vox.voxd.programs.subprocess_player.asyncio.create_subprocess_exec",
            _fake_exec,
        )
        source = FixedDirectory(tmp_path / "first")
        player = SubprocessPlayer(source)
        await (await player.play(Part("001.mp3", 1))).wait()
        source.switch(tmp_path / "second")
        await (await player.play(Part("001.mp3", 1))).wait()
        assert str(tmp_path / "first" / "001.mp3") in seen
        assert str(tmp_path / "second" / "001.mp3") in seen
