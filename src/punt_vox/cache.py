"""MP3 cache for quip phrases.

Caches synthesized audio files by (text, voice, provider) tuple so that
repeated hook invocations with the same quip phrase skip the TTS API
entirely.  Content-addressed by hash.

This module serves the **anonymous** cache only — calls that use the
ambient provider credential from ``keys.env``. Per-call provider
credential overrides (vox-a3e single-user multi-key billing isolation)
bypass this module entirely at the voxd call site: see the cache
guards in ``_synthesize_to_file`` in ``src/punt_vox/voxd.py``. That
design keeps all sensitive credential material out of any digest this
module computes. CodeQL's ``py/weak-sensitive-data-hashing`` rule
(correctly) flags any regular cryptographic hash — MD5, SHA-1,
SHA-256, and friends — as inappropriate for hashing password-class
input, and the only lint-clean alternatives are password KDFs
(Argon2, scrypt, bcrypt, PBKDF2 with high iteration counts) whose
per-call cost is unacceptable for a cache filename computation.

The bypass also closes a correctness hazard an earlier draft of PR
#175 had: a per-call billing scope that accepts cached bytes from
another scope is violating the whole point of the isolation. Scripts
that want cache hits for repeated quips should use ``keys.env`` (the
anonymous path); scripts that want billing attribution should accept
that every call re-synthesizes.

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
    """Compute the cache filename stem for a synthesis request.

    Anonymous-call cache only. Per-call credential overrides bypass
    the cache at the voxd call site (see the module docstring and
    the cache guards in ``_synthesize_to_file`` in
    ``src/punt_vox/voxd.py``) so no sensitive data ever reaches this
    function. The MD5 digest is byte-identical to pre-v4.2.1 format
    so existing on-disk cache entries remain reachable after
    upgrade. Filename shape: ``<32 hex chars>.mp3``.
    """
    payload = f"{text}\0{voice or ''}\0{provider or ''}".encode()
    # MD5 is deliberate here: the input contains no sensitive material
    # (text/voice/provider are non-secret), and the digest must stay
    # byte-identical to pre-v4.2.1 so existing on-disk cache entries
    # remain reachable after upgrade. Per-call credential overrides
    # never reach this function — see the module docstring.
    digest = hashlib.md5(payload).hexdigest()
    return f"{digest}.mp3"


def cache_get(text: str, voice: str | None, provider: str | None) -> Path | None:
    """Look up a cached MP3 file.

    Returns the path if the file exists and is non-empty.  Touches the
    file's mtime on hit so LRU eviction works correctly.  Returns None
    on miss. Anonymous cache only — per-call credential overrides
    bypass this layer entirely at the voxd call site so no sensitive
    data ever reaches ``cache_key``.
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
    text: str,
    voice: str | None,
    provider: str | None,
    source: Path,
) -> Path | None:
    """Copy a synthesized MP3 into the cache.

    Returns the cached path on success, or None if the source file
    does not exist or is empty.  Evicts oldest entries when the cache
    exceeds ``MAX_ENTRIES``. Anonymous cache only — per-call
    credential overrides bypass this layer entirely at the voxd call
    site so no sensitive data ever reaches ``cache_key``.
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
