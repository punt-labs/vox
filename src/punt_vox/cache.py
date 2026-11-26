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
from threading import Lock
from typing import ClassVar, Self

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
    vibe_tags: str | None = None  # Omit absent session cues to retain no-cue keys.

    @property
    def filename(self) -> str:
        """On-disk filename: 32-char MD5 hex + .mp3."""
        fields = asdict(self)
        if self.vibe_tags is None:
            del fields["vibe_tags"]
        payload = json.dumps(
            {"version": 3, **fields}, sort_keys=True, separators=(",", ":")
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
    with _CacheFiles.lock():
        path = key.path_in(CACHE_DIR)
        try:
            if path.stat().st_size == 0:
                logger.debug(
                    "Cache hit but empty file, treating as miss: %s", path.name
                )
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

    with _CacheFiles.lock():
        return _CacheFiles(source).publish(key.path_in(CACHE_DIR))


class _CacheFiles:
    """Coordinate cache file access and publish synthesized files atomically."""

    __slots__ = ("_source",)
    _locks: ClassVar[dict[str, Lock]] = {}
    _source: Path

    @classmethod
    def lock(cls) -> Lock:
        """Return the process-wide lock, publishing it once on concurrent first use."""
        lock = cls._locks.get("cache")
        if lock is None:
            lock = cls._locks.setdefault("cache", Lock())
        return lock

    def __new__(cls, source: Path) -> Self:
        """Bind the file to publish."""
        self = super().__new__(cls)
        self._source = source
        return self

    def publish(self, dest: Path) -> Path | None:
        """Commit the staged copy and keep the destination directory bounded."""
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Reject symlinks to prevent local symlink attacks
        if dest.is_symlink():
            logger.warning("Cache put: refusing symlink target %s", dest)
            return None
        self._stage(dest)
        logger.debug("Cache put: %s -> %s", self._source.name, dest.name)
        self._evict_if_needed(dest.parent)
        return dest

    def _stage(self, dest: Path) -> None:
        # Atomic write: copy to unique temp, then rename into place.
        # mkstemp guarantees no race if multiple processes cache concurrently.
        fd, tmp_str = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        tmp = Path(tmp_str)
        try:
            os.close(fd)
            shutil.copy2(self._source, tmp)
            tmp.rename(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _evict_if_needed(directory: Path) -> None:
        """Delete oldest entries by mtime when cache exceeds MAX_ENTRIES."""
        entries = sorted(directory.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for path in entries[: max(0, len(entries) - MAX_ENTRIES)]:
            path.unlink(missing_ok=True)
            logger.debug("Cache evict: %s", path.name)


def cache_clear() -> int:
    """Delete all cached MP3 and orphaned temp files.

    Returns the number of files deleted.
    """
    with _CacheFiles.lock():
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
    with _CacheFiles.lock():
        if not CACHE_DIR.exists():
            return CacheInfo(entries=0, size_bytes=0, path=CACHE_DIR)
        files = list(CACHE_DIR.glob("*.mp3")) + list(CACHE_DIR.glob("*.tmp"))
        total_size = sum(f.stat().st_size for f in files)
        return CacheInfo(entries=len(files), size_bytes=total_size, path=CACHE_DIR)
