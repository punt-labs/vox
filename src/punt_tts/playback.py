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
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_FILE = Path.home() / ".punt-tts" / "playback.lock"
AFPLAY_TIMEOUT = 120  # safety valve — no single audio should exceed 2 min
_PENDING_DIR = LOCK_FILE.parent / "pending"


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
    """Spawn detached subprocess that plays audio. Non-blocking.

    Copies the file to ``~/.punt-tts/pending/`` first so the original
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
    try:
        subprocess.Popen(
            [sys.executable, "-m", "punt_tts.playback", str(pending)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.exception("Failed to spawn playback subprocess for %s", path)
        pending.unlink(missing_ok=True)


if __name__ == "__main__":
    from punt_tts.logging_config import configure_logging

    configure_logging(stderr_level="WARNING")

    if len(sys.argv) < 2:
        logger.error("Usage: python -m punt_tts.playback <audio_file>")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    try:
        play_audio(audio_path)
    finally:
        # Clean up pending copies after playback completes.
        if audio_path.parent == _PENDING_DIR:
            audio_path.unlink(missing_ok=True)
