"""Serialized audio playback via flock.

Every playback invocation acquires LOCK_EX on a shared lock file,
runs the platform audio player synchronously, then releases.
Concurrent callers block on the lock and play in turn — no audio
is killed, no daemon needed.

The lock auto-releases on process exit (even crashes).

Player resolution: afplay (macOS) → ffplay (cross-platform, from ffmpeg).
fcntl.flock is POSIX (macOS + Linux).
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_FILE = Path.home() / ".punt-vox" / "playback.lock"
PLAYBACK_TIMEOUT = 120  # safety valve — no single audio should exceed 2 min
_PENDING_DIR = LOCK_FILE.parent / "pending"


def resolve_player() -> list[str]:
    """Return the command prefix for the platform audio player.

    Tries afplay (macOS native), then ffplay (cross-platform via ffmpeg).
    Raises FileNotFoundError if neither is available.
    """
    if shutil.which("afplay"):
        return ["afplay"]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    msg = (
        "No audio player found — install ffmpeg (provides ffplay)"
        " or use macOS (provides afplay)"
    )
    raise FileNotFoundError(msg)


def play_audio(path: Path) -> None:
    """Acquire flock, play audio, release. Blocking."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Waiting for playback lock: %s", path.name)
    with LOCK_FILE.open("w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        logger.info("Acquired playback lock, playing %s", path.name)
        try:
            cmd = resolve_player()
        except FileNotFoundError:
            logger.warning("No audio player found (need afplay or ffplay)")
            return
        try:
            subprocess.run(
                [*cmd, str(path)],
                check=False,
                timeout=PLAYBACK_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Playback timed out after %ds for %s", PLAYBACK_TIMEOUT, path
            )


def enqueue(path: Path) -> None:
    """Spawn detached subprocess that plays audio. Non-blocking.

    Copies the file to ``~/.punt-vox/pending/`` first so the original
    can be safely deleted (e.g., by ephemeral cleanup) before the
    subprocess acquires the flock and opens the file.
    """
    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pending = _PENDING_DIR / f"{os.getpid()}_{path.name}"
    try:
        shutil.copy2(path, pending)
    except OSError:
        logger.warning("Failed to copy %s for playback", path)
        return
    logger.info(
        "Enqueue playback: %s → %s",
        path.name,
        pending.name,
    )
    try:
        subprocess.Popen(
            [sys.executable, "-m", "punt_vox.playback", str(pending)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.exception("Failed to spawn playback subprocess for %s", path)
        pending.unlink(missing_ok=True)


if __name__ == "__main__":
    from punt_vox.logging_config import configure_logging

    configure_logging(stderr_level="WARNING")

    if len(sys.argv) < 2:
        logger.error("Usage: python -m punt_vox.playback <audio_file>")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    try:
        play_audio(audio_path)
    finally:
        # Clean up pending copies after playback completes.
        if audio_path.parent == _PENDING_DIR:
            audio_path.unlink(missing_ok=True)
