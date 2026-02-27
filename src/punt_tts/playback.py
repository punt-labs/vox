"""Serialized audio playback via flock.

Every playback invocation acquires LOCK_EX on a shared lock file,
runs afplay synchronously, then releases. Concurrent callers block
on the lock and play in turn — no audio is killed, no daemon needed.

The lock auto-releases on process exit (even crashes).

fcntl is macOS/Unix only — acceptable since afplay is macOS-only.
"""

from __future__ import annotations

import fcntl
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_FILE = Path.home() / ".punt-tts" / "playback.lock"
AFPLAY_TIMEOUT = 120  # safety valve — no single audio should exceed 2 min


def play_audio(path: Path) -> None:
    """Acquire flock, play via afplay, release. Blocking."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        logger.debug("Acquired playback lock, playing %s", path)
        try:
            subprocess.run(
                ["afplay", str(path)],
                check=False,
                timeout=AFPLAY_TIMEOUT,
            )
        except FileNotFoundError:
            logger.warning("afplay not found — auto-play requires macOS")
        except subprocess.TimeoutExpired:
            logger.warning("afplay timed out after %ds for %s", AFPLAY_TIMEOUT, path)


def enqueue(path: Path) -> None:
    """Spawn detached subprocess that calls play_audio. Non-blocking."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "punt_tts.playback", str(path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.warning("Failed to spawn playback subprocess for %s", path)


if __name__ == "__main__":
    play_audio(Path(sys.argv[1]))
