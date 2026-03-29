"""MP3 cache for quip phrases.

Caches synthesized audio files by (text, voice, provider) tuple so that
repeated hook invocations with the same quip phrase skip the TTS API
entirely.  Content-addressed via MD5 hash.

No dependencies on other vox modules — this is a standalone cache layer.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from punt_vox.logging_config import VOX_DATA_DIR

logger = logging.getLogger(__name__)

CACHE_DIR = VOX_DATA_DIR / "cache"
MAX_ENTRIES = 500


def cache_key(text: str, voice: str | None, provider: str | None) -> str:
    """Compute a deterministic cache filename from synthesis parameters.

    Returns an MD5-based filename like ``a1b2c3d4e5f6789012345678abcdef01.mp3``
    (32 hex characters).  The null byte separator ensures ``("ab", "c")``
    and ``("a", "bc")`` produce different keys.
    """
    payload = f"{text}\0{voice or ''}\0{provider or ''}"
    digest = hashlib.md5(payload.encode()).hexdigest()
    return f"{digest}.mp3"


def cache_get(text: str, voice: str | None, provider: str | None) -> Path | None:
    """Look up a cached MP3 file.

    Returns the path if the file exists and is non-empty.  Touches the
    file's mtime on hit so LRU eviction works correctly.  Returns None
    on miss.
    """
    path = CACHE_DIR / cache_key(text, voice, provider)
    if not path.exists():
        return None
    if path.stat().st_size == 0:
        logger.debug("Cache hit but empty file, treating as miss: %s", path.name)
        path.unlink(missing_ok=True)
        return None
    path.touch()
    logger.debug("Cache hit: %s", path.name)
    return path


def cache_put(
    text: str, voice: str | None, provider: str | None, source: Path
) -> Path | None:
    """Copy a synthesized MP3 into the cache.

    Returns the cached path on success, or None if the source file
    does not exist or is empty.  Evicts oldest entries when the cache
    exceeds ``MAX_ENTRIES``.
    """
    if not source.exists() or source.stat().st_size == 0:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = CACHE_DIR / cache_key(text, voice, provider)

    # Reject symlinks to prevent local symlink attacks
    if dest.is_symlink():
        logger.warning("Cache put: refusing symlink target %s", dest)
        return None

    # Atomic write: copy to unique temp, then rename into place.
    # mkstemp guarantees no race if multiple processes cache concurrently.
    fd, tmp_str = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        os.close(fd)
        shutil.copy2(source, tmp)
        tmp.rename(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.debug("Cache put: %s -> %s", source.name, dest.name)

    _evict_if_needed()
    return dest


def _evict_if_needed() -> None:
    """Delete oldest entries by mtime when cache exceeds MAX_ENTRIES."""
    if not CACHE_DIR.exists():
        return
    entries = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    excess = len(entries) - MAX_ENTRIES
    if excess <= 0:
        return
    for path in entries[:excess]:
        path.unlink(missing_ok=True)
        logger.debug("Cache evict: %s", path.name)


def cache_clear() -> int:
    """Delete all cached MP3 and orphaned temp files.

    Returns the number of files deleted.
    """
    if not CACHE_DIR.exists():
        return 0
    files = list(CACHE_DIR.glob("*.mp3")) + list(CACHE_DIR.glob("*.tmp"))
    for f in files:
        f.unlink(missing_ok=True)
    logger.info("Cache cleared: %d files", len(files))
    return len(files)


@dataclass(frozen=True)
class CacheInfo:
    """Cache status information."""

    entries: int
    size_bytes: int
    path: Path


def cache_status() -> CacheInfo:
    """Return current cache statistics (includes orphaned .tmp files)."""
    if not CACHE_DIR.exists():
        return CacheInfo(entries=0, size_bytes=0, path=CACHE_DIR)
    files = list(CACHE_DIR.glob("*.mp3")) + list(CACHE_DIR.glob("*.tmp"))
    total_size = sum(f.stat().st_size for f in files)
    return CacheInfo(entries=len(files), size_bytes=total_size, path=CACHE_DIR)
