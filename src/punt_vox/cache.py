"""MP3 cache for quip phrases.

Caches synthesized audio files by (text, voice, provider) tuple so that
repeated hook invocations with the same quip phrase skip the TTS API
entirely.  Content-addressed by hash.

The anonymous path (``api_key is None``) uses MD5 for backward
compatibility with cache entries written before per-call api_key support
existed. The api_key path uses SHA-256 because static analyzers (CodeQL
``py/weak-sensitive-data-hashing``) flag MD5 as inappropriate for any
digest that ingests secret material, even for non-password uses like
filenames. The two digests have different hex lengths (32 vs 64) so the
same cache directory holds both without any risk of collision between
paths.

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


def cache_key(
    text: str,
    voice: str | None,
    provider: str | None,
    api_key: str | None = None,
) -> str:
    """Compute a deterministic cache filename from synthesis parameters.

    When ``api_key`` is ``None`` (the default anonymous path, including
    the hook quip cache), the digest is
    ``md5("text\\0voice\\0provider")`` — **exactly** the three-segment
    payload used before per-call api_key support existed. Cache entries
    written by older vox versions therefore remain reachable after
    upgrade. Filename shape: ``<32 hex chars>.mp3``.

    When ``api_key`` is set (per-call billing isolation, vox-a3e), the
    digest is ``sha256("text\\0voice\\0provider\\0api_key")``. SHA-256
    is used instead of MD5 because CodeQL's
    ``py/weak-sensitive-data-hashing`` rule — correctly — flags MD5 as
    inappropriate for any hashing that ingests secret material, even
    for non-password use cases like filenames. SHA-256's collision
    resistance also eliminates any theoretical risk of a
    key-substitution attack producing a cache-key collision. Filename
    shape: ``<64 hex chars>.mp3``.

    The two digest lengths differ (32 hex vs 64 hex), so the anonymous
    and per-key filename spaces cannot collide and the same cache
    directory holds both without a subdirectory split.

    An empty string is normalized to ``None`` as defense-in-depth: the
    CLI already rejects ``--api-key ""`` via ``typer.BadParameter``
    before any call reaches this function, but normalizing here ensures
    a future caller that bypasses the CLI still falls into the
    backward-compat MD5 path for effectively-anonymous calls instead of
    silently landing in a separate SHA-256 partition keyed on the empty
    string.
    """
    if not api_key:
        api_key = None
    if api_key is None:
        payload = f"{text}\0{voice or ''}\0{provider or ''}".encode()
        # MD5 is deliberate on this branch: the input contains no
        # sensitive material (text/voice/provider are non-secret), and
        # the digest must stay byte-identical to pre-v4.2.1 so existing
        # on-disk cache entries remain reachable after upgrade. The
        # api_key branch below uses SHA-256.
        digest = hashlib.md5(payload).hexdigest()
        return f"{digest}.mp3"
    payload = f"{text}\0{voice or ''}\0{provider or ''}\0{api_key}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return f"{digest}.mp3"


def cache_get(
    text: str,
    voice: str | None,
    provider: str | None,
    api_key: str | None = None,
) -> Path | None:
    """Look up a cached MP3 file.

    Returns the path if the file exists and is non-empty.  Touches the
    file's mtime on hit so LRU eviction works correctly.  Returns None
    on miss. ``api_key`` partitions the cache per provider credential
    — see ``cache_key`` for the full rationale.
    """
    path = CACHE_DIR / cache_key(text, voice, provider, api_key)
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
    text: str,
    voice: str | None,
    provider: str | None,
    source: Path,
    api_key: str | None = None,
) -> Path | None:
    """Copy a synthesized MP3 into the cache.

    Returns the cached path on success, or None if the source file
    does not exist or is empty.  Evicts oldest entries when the cache
    exceeds ``MAX_ENTRIES``. ``api_key`` partitions the cache per
    provider credential — see ``cache_key`` for the full rationale.
    """
    if not source.exists() or source.stat().st_size == 0:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = CACHE_DIR / cache_key(text, voice, provider, api_key)

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
