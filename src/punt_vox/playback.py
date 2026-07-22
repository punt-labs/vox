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
import time
from pathlib import Path

from punt_vox.paths import user_state_dir

logger = logging.getLogger(__name__)

LOCK_FILE = user_state_dir() / "playback.lock"
PLAYBACK_TIMEOUT = 120  # safety valve — no single audio should exceed 2 min
# A clean exit under this almost certainly played nothing (missing audio
# backend, unreadable file the player opened then bailed on) -- the same
# heuristic the daemon's PlaybackQueue applies.
_SUSPICIOUS_ELAPSED_S = 0.05
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


def play_audio(path: Path) -> str | None:
    """Acquire flock, play audio, release. Blocking.

    Returns ``None`` on a clean, real-duration play, or a one-line failure
    detail when playback failed -- no player, a timeout, a non-zero player
    exit, or a clean exit so fast it almost certainly played nothing. The
    caller (``vox play`` of a local file) turns a non-``None`` result into a
    one-line error and a non-zero exit, matching the daemon store-ref path.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Waiting for playback lock: %s", path.name)
    with LOCK_FILE.open("w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        logger.info("Acquired playback lock, playing %s", path.name)
        try:
            cmd = resolve_player()
        except FileNotFoundError as exc:
            logger.warning("No audio player found (need afplay or ffplay)")
            return f"no audio player found: {exc}"
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [*cmd, str(path)],
                check=False,
                timeout=PLAYBACK_TIMEOUT,
                stderr=subprocess.PIPE,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Playback timed out after %ds for %s", PLAYBACK_TIMEOUT, path
            )
            return f"playback timed out after {PLAYBACK_TIMEOUT}s"
        detail = _classify_local_playback(
            proc.returncode, time.monotonic() - start, proc.stderr
        )
        if detail is not None:
            # Log the failure so the detached `python -m punt_vox.playback` path
            # (enqueue) stays diagnosable in vox.log even when the caller ignores
            # the returned detail.
            logger.warning("Playback failed for %s: %s", path.name, detail)
        return detail


def _classify_local_playback(
    rc: int | None, elapsed: float, stderr: bytes | None
) -> str | None:
    """Return ``None`` on a clean, real-duration play, else a failure detail."""
    detail = (stderr or b"").decode("utf-8", errors="replace").strip()
    if rc != 0:
        base = f"player exited rc={rc}"
        return f"{base}: {detail}" if detail else base
    if elapsed < _SUSPICIOUS_ELAPSED_S:
        return f"played nothing (elapsed {elapsed:.3f}s)"
    return None


def enqueue(path: Path) -> None:
    """Spawn detached subprocess that plays audio. Non-blocking.

    Copies the file to ``~/.punt-labs/vox/pending/`` first so the original
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
    from punt_vox.logging_config import configure_client_logging

    configure_client_logging(role="playback")

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
