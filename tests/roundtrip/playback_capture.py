"""Headless device capture, isolated from the developer's audio server."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest


def prerequisite(reason: str) -> None:
    if os.environ.get("VOX_PLAYBACK_REQUIRE") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def wait_for_file(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, f"process exited with {process.returncode}"
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {path.name}")


@contextmanager
def running(
    command: list[str], env: dict[str, str], cwd: Path
) -> Generator[subprocess.Popen[bytes]]:
    with (cwd / f"{Path(command[0]).name}.log").open("ab") as log:
        process = subprocess.Popen(command, env=env, cwd=cwd, stdout=log, stderr=log)
        try:
            yield process
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


@contextmanager
def pulse_environment(directory: Path) -> Generator[dict[str, str]]:
    for binary in ("pulseaudio", "pactl", "parec", "ffmpeg", "ffplay", "espeak-ng"):
        if shutil.which(binary) is None:
            prerequisite(f"playback prerequisite missing: {binary}")
    directory.mkdir(mode=0o700)
    env = dict(os.environ)
    env.update(
        HOME=str(directory),
        XDG_RUNTIME_DIR=str(directory),
        XDG_CONFIG_HOME=str(directory),
        XDG_CACHE_HOME=str(directory),
        SDL_AUDIODRIVER="pulse",
        PULSE_SINK="capture",
    )
    socket = directory / "pulse.sock"
    env["PULSE_SERVER"] = f"unix:{socket}"
    command = [
        "pulseaudio",
        "-n",
        "--daemonize=no",
        "--exit-idle-time=-1",
        "--use-pid-file=no",
        "--disable-shm=yes",
        "--load=module-null-sink sink_name=capture rate=22050 channels=1",
        f"--load=module-native-protocol-unix socket={socket} auth-anonymous=1",
    ]
    with running(command, env, directory) as process:
        deadline = time.monotonic() + 10
        while not socket.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                prerequisite("headless PulseAudio failed to start; see pulseaudio.log")
            time.sleep(0.05)
        subprocess.run(["pactl", "set-default-sink", "capture"], env=env, check=True)
        yield env


@contextmanager
def capture(directory: Path, env: dict[str, str]) -> Generator[Path]:
    raw, wav = directory / "monitor.raw", directory / "monitor.wav"
    with raw.open("wb") as output:
        process = subprocess.Popen(
            [
                "parec",
                "--device=capture.monitor",
                "--format=s16le",
                "--rate=22050",
                "--channels=1",
            ],
            env=env,
            stdout=output,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, "parec failed to start"
                sources = subprocess.check_output(
                    ["pactl", "list", "short", "source-outputs"], env=env
                )
                if sources.strip():
                    break
                assert time.monotonic() < deadline, "monitor recorder not ready"
                time.sleep(0.05)
            yield wav
            time.sleep(0.3)
        finally:
            process.terminate()
            process.communicate(timeout=10)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "s16le",
            "-ar",
            "22050",
            "-ac",
            "1",
            "-i",
            str(raw),
            str(wav),
        ],
        check=True,
    )
