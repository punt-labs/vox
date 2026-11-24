"""MP3 cache for quip phrases.

Caches synthesized audio by text, voice, provider, and audio-affecting settings
so repeated requests skip the TTS API. The versioned payload prevents serving
legacy entries whose keys omitted settings or whose audio predates postprocessing
changes.

This is the anonymous cache, using ambient provider credentials from keys.env.
Per-call credential overrides bypass lookup and store in
SynthesisPipeline.synthesize_to_file for billing isolation: cached bytes from
another billing scope must not satisfy a per-call credential request.

Credentials never enter CacheKey or its MD5 filename digest. CodeQL's
py/weak-sensitive-data-hashing rule flags regular hashes, including SHA-256,
for password-class inputs; password KDFs are inappropriate for cache filenames.
MD5 is used only for content addressing, not security.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from punt_vox.paths import user_state_dir

logger = logging.getLogger(__name__)

CACHE_DIR = user_state_dir() / "cache"
MAX_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Content-addressed identity for an anonymous synthesis.

    Includes text, voice, provider, and all audio-affecting settings, never
    credentials. The payload is versioned so legacy entries are not served.
    """

    text: str
    voice: str | None
    provider: str | None
    rate: int | None = None
    model: str | None = None
    language: str | None = None
    stability: float | None = None
    similarity: float | None = None
    style: float | None = None
    speaker_boost: bool | None = None

    @property
    def filename(self) -> str:
        """On-disk filename: 32-char MD5 hex + .mp3."""
        payload = json.dumps(
            {"version": 3, **asdict(self)}, sort_keys=True, separators=(",", ":")
        ).encode()
        digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
        return f"{digest}.mp3"

    def path_in(self, cache_dir: Path) -> Path:
        """Absolute path for this key in the given cache directory."""
        return cache_dir / self.filename


def cache_get(key: CacheKey) -> Path | None:
    """Look up a cached MP3 file.

    Returns the path if the file exists and is non-empty.  Touches the
    file's mtime on hit so LRU eviction works correctly.  Returns None
    on miss. Anonymous cache only — per-call credential overrides
    bypass this layer entirely at the voxd call site so no sensitive
    data ever reaches this function.
    """
    path = key.path_in(CACHE_DIR)
    try:
        if path.stat().st_size == 0:
            logger.debug("Cache hit but empty file, treating as miss: %s", path.name)
            path.unlink(missing_ok=True)
            return None
        os.utime(path, None)
    except FileNotFoundError:
        return None
    logger.debug("Cache hit: %s", path.name)
    return path


def cache_put(key: CacheKey, source: Path) -> Path | None:
    """Copy a synthesized MP3 into the cache.

    Returns the cached path on success, or None if the source file
    does not exist or is empty.  Evicts oldest entries when the cache
    exceeds ``MAX_ENTRIES``. Anonymous cache only — per-call
    credential overrides bypass this layer entirely at the voxd call
    site so no sensitive data ever reaches this function.
    """
    if not source.exists() or source.stat().st_size == 0:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = key.path_in(CACHE_DIR)

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


@dataclass(frozen=True, slots=True)
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
