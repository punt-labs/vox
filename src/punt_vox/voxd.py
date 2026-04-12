"""voxd -- audio server daemon.

Pure audio server. Receives synthesis requests over WebSocket,
synthesizes via configured providers, plays through speakers.
Knows nothing about MCP, hooks, projects, sessions, or Claude Code.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import importlib.resources
import json
import logging
import logging.config
import os
import platform
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from socket import socket
from typing import TYPE_CHECKING, cast

import typer
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from punt_vox import cache as _cache_module
from punt_vox.cache import cache_get, cache_put
from punt_vox.core import TTSClient
from punt_vox.normalize import normalize_for_speech
from punt_vox.paths import (
    config_dir as _user_config_dir,
    ensure_user_dirs,
    installed_version,
    log_dir as _user_log_dir,
    run_dir as _user_run_dir,
)
from punt_vox.providers import auto_detect_provider, get_provider
from punt_vox.resolve import split_leading_expressive_tags
from punt_vox.types import (
    AudioProviderId,
    AudioRequest,
    DirectPlayProvider,
    TTSProvider,
)

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421
DEFAULT_HOST = "127.0.0.1"

# Audio deduplication window: skip identical audio within this many seconds.
_DEDUP_WINDOW_SECONDS = 5.0

# Lock to serialize os.environ mutation during synthesis with per-request API keys.
_env_lock = asyncio.Lock()

# Mutex held by anything that produces audible sound. The playback queue
# consumer and the direct-play path both acquire it so that two clients
# (e.g. simultaneous hooks from two Claude sessions) can never overlap.
_playback_mutex = asyncio.Lock()


# ---------------------------------------------------------------------------
# Per-user state paths
#
# These are thin wrappers over ``punt_vox.paths`` so tests can monkey-patch
# them without reaching across modules. The source of truth is
# ``punt_vox.paths``; every path resolves to a subdirectory of
# ``~/.punt-labs/vox/`` — same on macOS and Linux.
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """Directory holding ``keys.env``.

    Pure path resolution — no ``mkdir``, no ``chmod``. ``main()``
    calls :func:`punt_vox.paths.ensure_user_dirs` at startup, which
    creates every per-user subdirectory with mode 0700 (and tightens
    pre-existing dirs that were created under a looser umask).
    Callers rely on that contract — this helper is a pure view of the
    path, nothing more.
    """
    return _user_config_dir()


def _log_dir() -> Path:
    """Directory holding ``voxd.log`` and rotated logs.

    Pure path resolution — see :func:`_config_dir`. Mode 0700 is
    guaranteed by the ``ensure_user_dirs`` call at the top of
    ``main()``; this helper does not create or chmod anything.
    """
    return _user_log_dir()


def _run_dir() -> Path:
    """Directory holding ``serve.port`` and ``serve.token``.

    Pure path resolution — see :func:`_config_dir`. Mode 0700 is
    guaranteed by the ``ensure_user_dirs`` call at the top of
    ``main()``.
    """
    return _user_run_dir()


# ---------------------------------------------------------------------------
# Logging (inline -- not importing logging_config.py)
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_MAX_BYTES = 5_242_880  # 5 MB
_LOG_BACKUP_COUNT = 5


_STARTUP_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "XDG_RUNTIME_DIR",
    "PULSE_SERVER",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "HOME",
    "USER",
    "LANG",
)


def _log_voxd_environment() -> None:
    """Log voxd's process identity and audio env vars at startup.

    Single greppable INFO line so operators can verify systemd env
    injection without poking at ``/proc`` (Linux-specific; macOS has no ``/proc``).
    """
    env = {k: os.environ.get(k, "<unset>") for k in _STARTUP_ENV_KEYS}
    # os.getuid/getgid are POSIX-only; fall back gracefully on Windows.
    getuid = cast("Callable[[], int] | None", getattr(os, "getuid", None))
    getgid = cast("Callable[[], int] | None", getattr(os, "getgid", None))
    uid: int | str = getuid() if getuid is not None else "<n/a>"
    gid: int | str = getgid() if getgid is not None else "<n/a>"
    logger.info(
        "voxd environment: pid=%d uid=%s gid=%s cwd=%s "
        "voxd_binary=%s voxd_module=%s env=%s",
        os.getpid(),
        uid,
        gid,
        os.getcwd(),
        sys.executable,
        __file__,
        env,
    )


def _configure_logging(log_dir: Path) -> None:
    """Configure logging with rotating file and stderr handlers.

    The log directory is expected to already exist at mode 0700 —
    :func:`punt_vox.paths.ensure_user_dirs` runs at the top of
    :func:`main` and creates (or tightens) every per-user subdirectory
    before the first log handler is attached. This function is pure
    logging configuration.
    """
    log_file = log_dir / "voxd.log"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": _LOG_FORMAT,
                    "datefmt": _LOG_DATE_FORMAT,
                },
            },
            "handlers": {
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": str(log_file),
                    "maxBytes": _LOG_MAX_BYTES,
                    "backupCount": _LOG_BACKUP_COUNT,
                    "encoding": "utf-8",
                    "formatter": "standard",
                    "level": "INFO",
                },
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                    "level": "INFO",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["file", "stderr"],
            },
            "loggers": {
                "boto3": {"level": "WARNING"},
                "botocore": {"level": "WARNING"},
                "urllib3": {"level": "WARNING"},
                "s3transfer": {"level": "WARNING"},
                "httpx": {"level": "WARNING"},
            },
        }
    )


# ---------------------------------------------------------------------------
# Key loading (inline -- not importing keys.py)
# ---------------------------------------------------------------------------

_PROVIDER_KEY_NAMES: frozenset[str] = frozenset(
    {
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "TTS_PROVIDER",
        "TTS_MODEL",
    }
)


def _load_keys(config_dir: Path) -> frozenset[str]:
    """Load keys.env from config dir into os.environ.

    Returns the names of variables that were loaded.
    """
    keys_file = config_dir / "keys.env"
    if not keys_file.exists():
        return frozenset()
    try:
        text = keys_file.read_text()
    except OSError as exc:
        logger.warning(
            "Could not read %s: %s -- daemon will use system TTS only",
            keys_file,
            exc,
        )
        return frozenset()
    loaded: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if key in _PROVIDER_KEY_NAMES and value and key not in os.environ:
            os.environ[key] = value
            loaded.add(key)
    return frozenset(loaded)


# ---------------------------------------------------------------------------
# Auth token management
# ---------------------------------------------------------------------------


def _read_or_create_token(run_dir: Path) -> str:
    """Read auth token from run dir, or generate a new one."""
    token_file = run_dir / "serve.token"
    if token_file.exists():
        try:
            token = token_file.read_text().strip()
        except (PermissionError, OSError) as exc:
            msg = (
                f"Cannot read auth token from {token_file}: {exc}. "
                "Fix file permissions or remove the file."
            )
            raise SystemExit(msg) from exc
        if not token:
            msg = f"Auth token file {token_file} is empty. Remove it to regenerate."
            raise SystemExit(msg)
        token_file.chmod(0o600)
        logger.info("Loaded auth token from %s", token_file)
        return token

    token = secrets.token_urlsafe(32)
    # The parent run dir is guaranteed to exist at mode 0700 by
    # ``ensure_user_dirs`` at the top of ``main()``. No defensive
    # mkdir here — it would just duplicate that contract.
    fd = os.open(str(token_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode())
    finally:
        os.close(fd)
    logger.info("Generated auth token at %s", token_file)
    return token


# ---------------------------------------------------------------------------
# Port file helpers
# ---------------------------------------------------------------------------


def _write_port_file(run_dir: Path, port: int) -> None:
    # ``run_dir`` is guaranteed to exist by the ``ensure_user_dirs``
    # call at the top of ``main()``; no defensive mkdir here.
    port_file = run_dir / "serve.port"
    port_file.write_text(str(port))
    logger.info("Wrote port file: %s (port %d)", port_file, port)


def _remove_port_file(run_dir: Path) -> None:
    port_file = run_dir / "serve.port"
    try:
        port_file.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove %s", port_file)
    logger.info("Removed port file")


def read_port_file() -> int | None:
    """Read the daemon port from the port file. Returns None if missing."""
    port_file = _run_dir() / "serve.port"
    try:
        return int(port_file.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def read_token_file() -> str | None:
    """Read the daemon auth token. Returns None if missing."""
    token_file = _run_dir() / "serve.token"
    try:
        return token_file.read_text().strip()
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Playback item and queue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaybackItem:
    """An item in the playback queue."""

    path: Path
    request_id: str
    notify: asyncio.Event


# Audio environment variables we capture for every playback. These determine
# whether ffplay can reach PulseAudio/PipeWire and dbus at the moment of the
# call, which is exactly the failure mode we saw on Linux in v4.0.3.
_AUDIO_ENV_KEYS: tuple[str, ...] = (
    "XDG_RUNTIME_DIR",
    "PULSE_SERVER",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "HOME",
    "USER",
)

# Playback under 50ms is a "success" that almost certainly played nothing.
_SUSPICIOUS_ELAPSED_S = 0.05

_PLAYBACK_TIMEOUT_S = 30.0

# Cap on the stderr blob we keep per playback. ffplay without -loglevel
# quiet can emit kilobytes of progress lines; we want enough for triage
# without unbounded growth in memory or log files.
_MAX_STDERR_LEN = 2000


def _truncate_stderr(text: str) -> str:
    """Return ``text`` clipped to ``_MAX_STDERR_LEN`` with head + tail kept."""
    if len(text) <= _MAX_STDERR_LEN:
        return text
    half = _MAX_STDERR_LEN // 2
    dropped = len(text) - _MAX_STDERR_LEN
    return f"{text[:half]}\n... [truncated {dropped} bytes] ...\n{text[-half:]}"


def _monotonic() -> float:
    """Indirection for ``time.monotonic`` so tests can stub playback timing.

    Patching ``time.monotonic`` directly would also affect asyncio internals.
    """
    return time.monotonic()


def _snapshot_env(keys: tuple[str, ...]) -> dict[str, str]:
    """Return a dict of env var values, using <unset> for missing keys."""
    return {k: os.environ.get(k, "<unset>") for k in keys}


def _is_darwin() -> bool:
    """Return True on macOS.

    Wrapped in a function so mypy doesn't narrow ``sys.platform`` to a
    single value at the call site, which would mark the non-matching
    branch as unreachable for cross-platform development.
    """
    return platform.system() == "Darwin"


def _player_binary_name() -> str:
    """Return the platform player binary name."""
    return "afplay" if _is_darwin() else "ffplay"


def _player_binary_path() -> str | None:
    """Return the resolved path to the platform player binary, or None."""
    return shutil.which(_player_binary_name())


def _player_command(path: Path) -> list[str]:
    """Return the argv for playing ``path`` on this platform.

    No ``-loglevel quiet`` on ffplay -- we want its stream summary and errors.
    """
    if _is_darwin():
        return ["afplay", str(path)]
    return ["ffplay", "-nodisp", "-autoexit", str(path)]


_MUSIC_VOLUME = 30


def _music_player_command(path: Path) -> list[str]:
    """Return the argv for playing a music track at reduced volume.

    Music plays at ``-volume 30`` so speech and chimes overlay on top
    at full volume without runtime volume manipulation.
    """
    if _is_darwin():
        return ["afplay", "--volume", "0.3", str(path)]
    return ["ffplay", "-nodisp", "-autoexit", "-volume", str(_MUSIC_VOLUME), str(path)]


def _music_output_dir() -> Path:
    """Return the directory for generated music tracks."""
    from punt_vox.output import default_output_dir

    return default_output_dir() / "music"


def _record_playback_result(
    ctx: DaemonContext,
    *,
    path: Path,
    rc: int,
    elapsed: float,
    stderr: str,
) -> None:
    """Update ctx.last_playback with a freshly-observed playback result."""
    ctx.last_playback = {
        "file": str(path),
        "rc": rc,
        "elapsed_s": round(elapsed, 4),
        "stderr": stderr,
        "ts": time.time(),
    }


async def _play_audio(path: Path, ctx: DaemonContext) -> None:
    """Play an audio file and record a rich result in ``ctx.last_playback``.

    Captures spawn command, audio env vars at call time, exit code, elapsed
    wall time, file size, and full stderr. Logs ERROR on non-zero exit,
    WARNING on suspiciously fast "success", INFO with stderr summary on
    normal success. Stderr is never silently discarded.
    """
    cmd = _player_command(path)
    env_snapshot = _snapshot_env(_AUDIO_ENV_KEYS)

    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.error("Playback aborted: cannot stat %s: %s", path, exc)
        _record_playback_result(
            ctx, path=path, rc=-1, elapsed=0.0, stderr=f"stat failed: {exc}"
        )
        return

    if size == 0:
        logger.error(
            "Playback aborted: 0-byte audio file %s -- synthesis bug upstream",
            path,
        )
        _record_playback_result(
            ctx, path=path, rc=-1, elapsed=0.0, stderr="0-byte file"
        )
        return

    logger.info(
        "Playback spawn: cmd=%s size=%d audio_env=%s",
        cmd,
        size,
        env_snapshot,
    )

    start = _monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        elapsed = _monotonic() - start
        logger.error(
            "Playback FAILED: binary not found: %s (%s) cmd=%s audio_env=%s",
            cmd[0],
            exc,
            cmd,
            env_snapshot,
        )
        _record_playback_result(
            ctx,
            path=path,
            rc=-1,
            elapsed=elapsed,
            stderr=f"FileNotFoundError: {exc}",
        )
        return
    except OSError as exc:
        elapsed = _monotonic() - start
        logger.error(
            "Playback FAILED: OSError spawning %s: %s audio_env=%s",
            cmd[0],
            exc,
            env_snapshot,
        )
        _record_playback_result(
            ctx, path=path, rc=-1, elapsed=elapsed, stderr=f"OSError: {exc}"
        )
        return

    try:
        _, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=_PLAYBACK_TIMEOUT_S
        )
    except TimeoutError:
        elapsed = _monotonic() - start
        logger.error(
            "Playback FAILED: timed out after %.1fs for %s audio_env=%s",
            _PLAYBACK_TIMEOUT_S,
            path.name,
            env_snapshot,
        )
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        _record_playback_result(
            ctx,
            path=path,
            rc=-1,
            elapsed=elapsed,
            stderr=f"timeout after {_PLAYBACK_TIMEOUT_S}s",
        )
        return

    elapsed = _monotonic() - start
    rc = proc.returncode if proc.returncode is not None else -1
    raw_stderr = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
    stderr_text = _truncate_stderr(raw_stderr)

    _record_playback_result(ctx, path=path, rc=rc, elapsed=elapsed, stderr=stderr_text)

    if rc != 0:
        logger.error(
            "Playback FAILED: rc=%d elapsed=%.3fs file=%s size=%d "
            "cmd=%s audio_env=%s stderr=%r",
            rc,
            elapsed,
            path.name,
            size,
            cmd,
            env_snapshot,
            stderr_text,
        )
        return

    if elapsed < _SUSPICIOUS_ELAPSED_S:
        logger.warning(
            "Playback SUSPICIOUS: rc=0 but elapsed=%.4fs (<%.2fs) file=%s "
            "size=%d audio_env=%s stderr=%r -- probably played nothing",
            elapsed,
            _SUSPICIOUS_ELAPSED_S,
            path.name,
            size,
            env_snapshot,
            stderr_text,
        )
        return

    if stderr_text:
        logger.info(
            "Playback ok: elapsed=%.3fs file=%s size=%d stderr=%r",
            elapsed,
            path.name,
            size,
            stderr_text,
        )
    else:
        logger.info(
            "Playback ok: elapsed=%.3fs file=%s size=%d",
            elapsed,
            path.name,
            size,
        )


async def _playback_consumer(ctx: DaemonContext) -> None:
    """Single consumer: plays audio sequentially.

    Holds ``_playback_mutex`` for the duration of each item so the
    direct-play path can't produce overlapping audio from another
    coroutine.
    """
    while True:
        item = await ctx.playback_queue.get()
        logger.info("Playback start: %s", item.path.name)
        async with _playback_mutex:
            await _play_audio(item.path, ctx)
        logger.info("Playback done: %s", item.path.name)
        item.notify.set()
        ctx.playback_queue.task_done()


# ---------------------------------------------------------------------------
# Audio deduplication
# ---------------------------------------------------------------------------


class ChimeDedup:
    """Always-on in-memory dedup for chime signals.

    Chimes are event markers (tests-pass, lint-fail, git-push-ok, etc)
    and a user does not want to hear the same event chime twice in rapid
    succession. Unlike speech, chime deduplication is always on and
    keyed only on the signal name. The window matches the legacy
    `AudioDedup` default so the user-visible chime behavior is
    unchanged from versions prior to vox-0e9.
    """

    def __init__(self, window: float = _DEDUP_WINDOW_SECONDS) -> None:
        self._window = window
        self._seen: dict[str, float] = {}

    def should_play(self, signal: str) -> bool:
        """Return True if this chime should play (not a recent duplicate)."""
        key = hashlib.md5(f"chime:{signal}".encode()).hexdigest()
        now = time.monotonic()
        last = self._seen.get(key)
        if last is not None and (now - last) < self._window:
            return False
        self._seen[key] = now
        cutoff = now - self._window * 2
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        return True


@dataclass(frozen=True)
class DedupHit:
    """Returned when an opt-in speech dedup catches a duplicate request.

    The caller made a synthesize/direct_play request with ``once=<ttl>``
    and an identical text was already played within the TTL window. The
    user has already heard the message — the caller should NOT treat
    this as an error or retry. The fields support observability (logging
    "wall skipped, already played 53s ago") and future UI surfaces.
    """

    original_played_at: float
    """Wall-clock ``time.time()`` of the original play, in seconds since
    the epoch. Safe to serialize, safe to compare against other wall
    clocks. NOT ``time.monotonic()``."""

    ttl_seconds_remaining: float
    """How many more seconds this dedup key remains valid. When the
    TTL expires, the next identical request will play fresh."""


_ONCE_DEDUP_MAX_TTL_SECONDS: float = 3600.0
"""Hard cap on per-call ``once`` TTL to bound memory usage. Any caller
passing a larger value is clamped to this cap with a log warning. The
biff wall use case targets 600 seconds; 3600 is 6x that, more than
enough headroom without letting a stray ``once=99999999`` wedge
``OnceDedup._seen`` with long-lived entries."""

_ONCE_DEDUP_MAX_ENTRIES: int = 1024
"""Hard cap on the number of tracked keys. Entries are evicted in
insertion order (oldest first) when the cap is reached. Defensive
against pathological workloads that insert thousands of unique texts
faster than the time-based pruner can drop them."""


class OnceDedup:
    """Opt-in in-memory dedup for speech with per-call TTL.

    Callers pass ``once=<ttl_seconds>`` on a synthesize or direct_play
    WebSocket message (or the ``vox unmute --once <seconds>`` CLI flag)
    to suppress duplicate plays of identical text within their chosen
    window. Identical text spoken with different voices or providers
    collapses — the dedup key is ``md5(text)`` only.

    The motivating use case is ``biff wall``: N Claude Code sessions
    in the same repo independently shell out to ``vox unmute`` on the
    same broadcast text, and the user should hear the announcement
    exactly once. See bead vox-0e9.

    Unlike the legacy always-on ``AudioDedup``, this class is only
    invoked when the caller explicitly opts in. Requests without an
    ``once`` parameter play every time, even if identical to a recent
    one. This preserves the property that ``vox unmute "hello"`` twice
    in quick succession on the CLI produces two audible plays.

    Per-caller TTL semantics: each caller's ``ttl_seconds`` applies to
    THEIR query, not to the stored entry. The dedup question each
    caller asks is "was this text played in the last ttl_seconds?" — a
    caller passing ``once=60`` will see dedup only if the original play
    was within 60 seconds, regardless of whether an earlier caller
    passed ``once=600``. This matches the intuitive "dedupe within N
    seconds" semantic.

    Concurrency: ``check_and_record`` is atomic under voxd's
    single-threaded asyncio event loop. A failed synthesis path MUST
    call ``rollback`` to remove the zombie entry; otherwise the next
    identical call would be incorrectly deduped against a playback
    that never happened.
    """

    def __init__(self) -> None:
        # key -> (inserted_monotonic, inserted_wall_clock)
        self._seen: dict[str, tuple[float, float]] = {}

    def check_and_record(self, text: str, ttl_seconds: float) -> DedupHit | None:
        """Check for a recent duplicate; record this call if none found.

        Args:
            text: The speech text. Used as the dedup key via ``md5``.
            ttl_seconds: Dedup window in seconds. Must be positive.
                Values above ``_ONCE_DEDUP_MAX_TTL_SECONDS`` are
                clamped to the cap with a log warning.

        Returns:
            ``None`` if no duplicate exists within the window — the
            caller should proceed with synthesis + playback and call
            ``rollback(text)`` on failure so the zombie entry is
            removed.
            ``DedupHit(...)`` if a duplicate was found — the caller
            should skip the play and return the hit to its client so
            the client can render an observable "deduped" response.

        Raises:
            ValueError: if ``ttl_seconds`` is zero or negative.
        """
        if ttl_seconds <= 0:
            msg = f"ttl_seconds must be positive, got {ttl_seconds}"
            raise ValueError(msg)
        if ttl_seconds > _ONCE_DEDUP_MAX_TTL_SECONDS:
            logger.warning(
                "OnceDedup: ttl_seconds=%.1f exceeds cap %.1f, clamping",
                ttl_seconds,
                _ONCE_DEDUP_MAX_TTL_SECONDS,
            )
            ttl_seconds = _ONCE_DEDUP_MAX_TTL_SECONDS

        key = hashlib.md5(text.encode()).hexdigest()
        now_mono = time.monotonic()
        now_wall = time.time()

        existing = self._seen.get(key)
        if existing is not None:
            inserted_mono, inserted_wall = existing
            age = now_mono - inserted_mono
            # Per-caller semantics: the dedup fires only if the age is
            # within THIS caller's ttl_seconds, not the stored entry's.
            if age < ttl_seconds:
                return DedupHit(
                    original_played_at=inserted_wall,
                    ttl_seconds_remaining=ttl_seconds - age,
                )

        self._seen[key] = (now_mono, now_wall)

        # Opportunistic time-based prune: drop entries older than the
        # cap so we never accumulate entries beyond the cap horizon.
        cutoff = now_mono - _ONCE_DEDUP_MAX_TTL_SECONDS
        self._seen = {k: (m, w) for k, (m, w) in self._seen.items() if m > cutoff}

        # Hard cap on dict size. If somehow the time prune left us with
        # more than _ONCE_DEDUP_MAX_ENTRIES, evict oldest-first. This is
        # defensive against pathological inserts at a rate faster than
        # the time pruner can keep up.
        if len(self._seen) > _ONCE_DEDUP_MAX_ENTRIES:
            sorted_items = sorted(self._seen.items(), key=lambda kv: kv[1][0])
            keep = sorted_items[-_ONCE_DEDUP_MAX_ENTRIES:]
            self._seen = dict(keep)

        return None

    def rollback(self, text: str) -> None:
        """Remove a recorded entry for *text*, if present.

        Used when synthesis or playback fails after
        ``check_and_record`` returned None: without a rollback, the
        zombie entry would incorrectly dedup subsequent retries
        against a playback that never happened. Idempotent — safe
        to call when no entry exists.
        """
        key = hashlib.md5(text.encode()).hexdigest()
        self._seen.pop(key, None)


# ---------------------------------------------------------------------------
# Daemon context
# ---------------------------------------------------------------------------


class DaemonContext:
    """Shared mutable state for the voxd process."""

    def __init__(
        self,
        *,
        auth_token: str | None = None,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.start_time: float = time.monotonic()
        self.auth_token: str | None = auth_token
        self.port: int = port
        self.chime_dedup = ChimeDedup()
        self.once_dedup = OnceDedup()
        self.client_count: int = 0
        self.playback_queue: asyncio.Queue[PlaybackItem] = asyncio.Queue()
        self.last_playback: dict[str, object] | None = None
        # Cached once at startup so /health does not hit importlib.metadata
        # on every request. See ``punt_vox.paths.installed_version`` for
        # fallback semantics when running from an uninstalled source tree.
        self.daemon_version: str = installed_version()
        # Music state — daemon-wide, one set of speakers, one loop.
        self.music_mode: str = "off"
        self.music_style: str = ""
        self.music_owner: str = ""
        self.music_vibe: tuple[str, str] = ("", "")  # (vibe, vibe_tags)
        self.music_track: Path | None = None
        self.music_track_name: str = ""
        self.music_proc: asyncio.subprocess.Process | None = None
        self.music_state: str = "idle"  # "idle" | "generating" | "playing"
        self.music_changed: asyncio.Event = asyncio.Event()


# ---------------------------------------------------------------------------
# Chime asset resolution
# ---------------------------------------------------------------------------

_CHIME_MAP: dict[str, str] = {
    "done": "chime_done.mp3",
    "prompt": "chime_prompt.mp3",
    "acknowledge": "chime_done.mp3",
    "compact": "chime_done.mp3",
    "subagent": "chime_done.mp3",
    "farewell": "chime_done.mp3",
    "tests-pass": "chime_tests_pass.mp3",
    "tests-fail": "chime_tests_fail.mp3",
    "lint-pass": "chime_lint_pass.mp3",
    "lint-fail": "chime_lint_fail.mp3",
    "git-push-ok": "chime_git_push_ok.mp3",
    "merge-conflict": "chime_merge_conflict.mp3",
}


def _resolve_chime(signal: str) -> Path | None:
    """Resolve a chime signal name to a bundled asset path."""
    filename = _CHIME_MAP.get(signal)
    if filename is None:
        return None
    try:
        ref = importlib.resources.files("punt_vox.assets").joinpath(filename)
        # as_file returns a context manager; we need the actual path.
        # For installed packages the file is already on disk.
        path = Path(str(ref))
        if path.exists():
            return path
    except (TypeError, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _check_auth(websocket: WebSocket, ctx: DaemonContext) -> bool:
    """Verify the auth token from query param or first message."""
    if ctx.auth_token is None:
        return True  # No auth configured (tests)
    token = websocket.query_params.get("token", "")
    return hmac.compare_digest(token, ctx.auth_token)


# ---------------------------------------------------------------------------
# WebSocket message handlers
# ---------------------------------------------------------------------------


def _parse_optional_float(msg: dict[str, object], key: str) -> float | None:
    """Extract an optional float field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return float(str(raw))


def _parse_optional_int(msg: dict[str, object], key: str) -> int | None:
    """Extract an optional int field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return int(str(raw))


def _parse_optional_str(msg: dict[str, object], key: str) -> str | None:
    """Extract an optional string field, returning None for empty strings."""
    raw = str(msg.get(key, ""))
    return raw or None


def _build_audio_request(
    normalized_text: str,
    voice: str | None,
    language: str | None,
    rate: int | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool | None,
    provider_id: str,
) -> AudioRequest:
    """Build an AudioRequest from parsed message fields."""
    return AudioRequest(
        text=normalized_text,
        voice=voice,
        language=language,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
        provider=AudioProviderId(provider_id)
        if provider_id in AudioProviderId.__members__
        else None,
    )


def _model_supports_expressive_tags(provider_name: str, model: str | None) -> bool:
    """Whether the given provider+model combo interprets bracket-style tags.

    Pure lookup: does NOT construct the provider or touch any SDK client,
    so it can run before voxd enters the env-mutation lock that the real
    synthesize path needs. ElevenLabs is the only provider whose answer
    depends on the model — all others return False unconditionally.

    The ``ElevenLabsProvider`` import is deferred inside the function so
    voxd does not eagerly load the ElevenLabs SDK at module import time
    on systems whose users only ever run espeak/say. Mirrors the lazy
    pattern in :mod:`punt_vox.providers`.
    """
    if provider_name == "elevenlabs":
        from punt_vox.providers.elevenlabs import ElevenLabsProvider

        return ElevenLabsProvider.model_supports_expressive_tags(model)
    return False


def _apply_vibe_for_synthesis(
    raw_text: str,
    vibe_tags: str | None,
    provider_name: str,
    model: str | None,
) -> str:
    """Compose the final synthesis text from raw input + vibe + capability.

    Takes the user's RAW ``raw_text`` (NOT yet normalized). The order of
    operations matters because :func:`punt_vox.normalize.normalize_for_speech`
    discards brackets via its non-prosody-punctuation filter. If we let
    normalization run before stripping or before splitting tags, then
    ``[serious] hello`` becomes ``serious hello`` and the literal word
    ``serious`` survives into the TTS input on every non-expressive
    provider. We have to peel the leading tags off first.

    Steps:

    1. Split leading bracket tags off the raw text into ``leading_tags``
       and ``raw_body``. The split is whitespace-aware and only matches
       at the very front of the string — embedded ``[tag]`` mid-sentence
       is left to normalization (which strips its brackets).
    2. Run ``normalize_for_speech`` on ``raw_body`` only, never on the
       tags themselves.
    3. If the active model supports expressive tags, re-attach them in
       order: ``vibe_tags`` (session-level) first, then the user's own
       leading tags, then the normalized body.
    4. If the active model does NOT support expressive tags, drop both
       ``vibe_tags`` and the user's leading tags. Return only the
       normalized body so the TTS engine never sees a bracket character
       or the bare word inside one.
    """
    expressive = _model_supports_expressive_tags(provider_name, model)

    leading_tags, raw_body = split_leading_expressive_tags(raw_text)
    body = normalize_for_speech(raw_body)

    if not expressive:
        # Drop both vibe_tags and user-supplied leading tags. The body has
        # no bracket characters left because we split them off above.
        return body

    parts: list[str] = []
    if vibe_tags:
        parts.append(vibe_tags.strip())
    if leading_tags:
        parts.append(leading_tags)
    if body:
        parts.append(body)
    return " ".join(parts)


async def _synthesize_to_file(
    text: str,
    voice: str | None,
    provider_name: str,
    model: str | None,
    language: str | None,
    rate: int | None,
    vibe_tags: str | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool | None,
    api_key: str | None,
    request_id: str = "",
) -> Path:
    """Run TTS synthesis and return the output path.

    Handles API key injection, provider construction, and caching.
    Raises on failure.

    When ``api_key`` is set, the cache is bypassed on both the lookup
    and the store so the per-call billing scope (vox-a3e) never reads
    bytes that were synthesized under a different key and never leaves
    bytes behind that a later call on a different key could reuse. The
    anonymous path (``api_key is None``) uses the MD5-keyed on-disk
    cache unchanged. See ``src/punt_vox/cache.py`` for the rationale.
    """
    resolved_voice = voice or ""

    # _apply_vibe_for_synthesis takes RAW text and runs normalize_for_speech
    # on the body itself (after splitting leading bracket tags off, which
    # would otherwise be eaten by normalization).
    normalized = _apply_vibe_for_synthesis(text, vibe_tags, provider_name, model)

    # Cache lookup: anonymous calls only. Per-call api_key scopes
    # bypass the cache entirely so a billing-isolated call never
    # reads bytes synthesized under a different key (or no key).
    # CodeQL py/weak-sensitive-data-hashing also required that we
    # never feed the api_key into any digest in cache.py.
    if api_key is None:
        cached = cache_get(normalized, resolved_voice, provider_name)
        if cached is not None:
            return cached
    else:
        logger.debug(
            "Per-call api_key set; bypassing cache for this request (id=%s)",
            request_id,
        )

    # Serialize env mutation + synthesis to avoid concurrent os.environ races.
    async with _env_lock:
        old_key: str | None = None
        env_key_name: str | None = None
        if api_key:
            if provider_name == "elevenlabs":
                env_key_name = "ELEVENLABS_API_KEY"
            elif provider_name == "openai":
                env_key_name = "OPENAI_API_KEY"
            if env_key_name:
                old_key = os.environ.get(env_key_name)
                os.environ[env_key_name] = api_key

        try:
            provider = get_provider(provider_name, config_path=None, model=model)
            request = _build_audio_request(
                normalized,
                voice,
                language,
                rate,
                stability,
                similarity,
                style,
                speaker_boost,
                provider_name,
            )
            client = TTSClient(provider)

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                output_path = Path(tmp.name)

            await asyncio.to_thread(client.synthesize, request, output_path)

            try:
                synth_size = output_path.stat().st_size
            except OSError:
                synth_size = -1
            if synth_size <= 0:
                logger.error(
                    "synthesize FAILED: provider=%s voice=%s file=%s "
                    "size=%d chars_in=%d -- zero-byte or missing output",
                    provider_name,
                    resolved_voice,
                    output_path,
                    synth_size,
                    len(text),
                )
                # Delete the broken temp file and fail fast. Caching it
                # would poison every subsequent identical request.
                output_path.unlink(missing_ok=True)
                msg = (
                    f"synthesis produced missing or empty output file: "
                    f"{output_path} (provider={provider_name}, "
                    f"voice={resolved_voice}, chars_in={len(text)})"
                )
                raise RuntimeError(msg)

            logger.info(
                "synthesize done: provider=%s voice=%s file=%s size=%d chars_in=%d",
                provider_name,
                resolved_voice,
                output_path,
                synth_size,
                len(text),
            )

            # Only cache verified-good output, and only on the
            # anonymous path. Per-call api_key scopes skip cache_put
            # so a billing-isolated call can never leave bytes behind
            # that a later call on a different key could reuse.
            if api_key is None:
                cache_put(normalized, resolved_voice, provider_name, output_path)
            return output_path
        finally:
            # Restore API key
            if env_key_name and old_key is not None:
                os.environ[env_key_name] = old_key
            elif env_key_name and api_key:
                os.environ.pop(env_key_name, None)


# Providers that synthesize audio directly to the default device. Cloud
# providers are skipped entirely so we don't pay for provider construction
# only to discover they don't implement play_directly.
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"espeak", "say"})

# Map of provider name to its expected API key env var. Used by the
# direct-play env-injection helper.
_PROVIDER_API_KEY_VAR: dict[str, str] = {
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _run_play_directly_sync(
    provider_name: str,
    api_key: str | None,
    provider_factory: Callable[[], TTSProvider],
    request: AudioRequest,
) -> int | None:
    """Construct provider and call ``play_directly`` on a worker thread.

    Returns ``None`` if the provider does not implement the
    ``DirectPlayProvider`` protocol -- the caller will fall back to the
    synthesize-and-queue path. Mutates ``os.environ`` only if an API key
    is supplied; restoration happens on the same thread so the env-lock
    contract is preserved without holding the lock during audio playback.
    """
    env_var = _PROVIDER_API_KEY_VAR.get(provider_name) if api_key else None
    old_value: str | None = None
    if env_var and api_key:
        old_value = os.environ.get(env_var)
        os.environ[env_var] = api_key
    try:
        provider = provider_factory()
        if not isinstance(provider, DirectPlayProvider):
            return None
        return provider.play_directly(request)
    finally:
        if env_var:
            if old_value is not None:
                os.environ[env_var] = old_value
            else:
                os.environ.pop(env_var, None)


async def _try_direct_play(
    *,
    text: str,
    voice: str | None,
    provider_name: str,
    model: str | None,
    language: str | None,
    rate: int | None,
    vibe_tags: str | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool | None,
    api_key: str | None,
    ctx: DaemonContext,
) -> int | None | Exception:
    """Attempt direct-to-device playback via the provider.

    Returns one of:
      * an ``int`` exit code (0 on success) when ``play_directly`` ran,
      * ``None`` when the provider opts out of direct play, or
      * an ``Exception`` instance when provider construction or playback
        raised. The caller is responsible for translating the exception
        into a websocket error response.

    The ``_env_lock`` is only acquired when an API key needs to be
    injected. Local providers (espeak, say) take a fast path with no
    cross-request blocking. Audio playback never holds the lock.
    """
    # _apply_vibe_for_synthesis takes RAW text and runs normalize_for_speech
    # on the body itself (after splitting leading bracket tags off, which
    # would otherwise be eaten by normalization).
    normalized = _apply_vibe_for_synthesis(text, vibe_tags, provider_name, model)

    request = _build_audio_request(
        normalized,
        voice,
        language,
        rate,
        stability,
        similarity,
        style,
        speaker_boost,
        provider_name,
    )

    def _factory() -> TTSProvider:
        return get_provider(provider_name, config_path=None, model=model)

    start = _monotonic()
    try:
        # _playback_mutex serializes audible output across all paths --
        # the queue consumer holds it too. Without this, two hooks firing
        # at once would overlap because direct-play bypasses the queue.
        if api_key and provider_name in _PROVIDER_API_KEY_VAR:
            async with _env_lock, _playback_mutex:
                rc = await asyncio.to_thread(
                    _run_play_directly_sync,
                    provider_name,
                    api_key,
                    _factory,
                    request,
                )
        else:
            async with _playback_mutex:
                rc = await asyncio.to_thread(
                    _run_play_directly_sync,
                    provider_name,
                    None,
                    _factory,
                    request,
                )
    except Exception as exc:
        logger.exception("Direct-play raised for provider=%s", provider_name)
        return exc

    if rc is None:
        return None

    elapsed = _monotonic() - start
    _record_playback_result(
        ctx,
        path=Path(f"<direct:{provider_name}>"),
        rc=rc,
        elapsed=elapsed,
        stderr="" if rc == 0 else f"play_directly rc={rc}",
    )
    if rc == 0:
        logger.info(
            "Direct-play ok: provider=%s voice=%s elapsed=%.3fs chars=%d",
            provider_name,
            voice or "",
            elapsed,
            len(text),
        )
    else:
        logger.error(
            "Direct-play FAILED: provider=%s voice=%s elapsed=%.3fs rc=%d",
            provider_name,
            voice or "",
            elapsed,
            rc,
        )
    return rc


async def _handle_synthesize(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'synthesize' message: TTS + enqueue playback."""
    request_id = str(msg.get("id", ""))
    text = str(msg.get("text", ""))
    if not text:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "empty text"}
        )
        return

    voice = _parse_optional_str(msg, "voice")
    provider_name = _parse_optional_str(msg, "provider") or auto_detect_provider()
    model = _parse_optional_str(msg, "model")
    rate = _parse_optional_int(msg, "rate")
    language = _parse_optional_str(msg, "language")
    vibe_tags = _parse_optional_str(msg, "vibe_tags")
    stability = _parse_optional_float(msg, "stability")
    similarity = _parse_optional_float(msg, "similarity")
    style = _parse_optional_float(msg, "style")
    speaker_boost_raw = msg.get("speaker_boost")
    speaker_boost = bool(speaker_boost_raw) if speaker_boost_raw is not None else None
    api_key = _parse_optional_str(msg, "api_key")
    once = _parse_optional_int(msg, "once")

    resolved_voice = voice or ""

    # Opt-in dedup: only when the caller explicitly sets `once` to a
    # positive TTL. With `once` absent, null, or 0, every request plays
    # — the legacy always-on 5s dedup for speech was removed in vox-0e9.
    # When we record an entry, track that fact so we can roll it back
    # on synthesis/playback failure; otherwise a failed request would
    # leave a zombie dedup entry that incorrectly suppresses retries.
    dedup_recorded = False
    if once is not None and once > 0:
        hit = ctx.once_dedup.check_and_record(text, float(once))
        if hit is not None:
            logger.info(
                "Dedup hit: id=%s text=%d chars original_played_at=%.3f "
                "ttl_remaining=%.1fs",
                request_id,
                len(text),
                hit.original_played_at,
                hit.ttl_seconds_remaining,
            )
            await websocket.send_json(
                {
                    "type": "done",
                    "id": request_id,
                    "deduped": True,
                    "original_played_at": hit.original_played_at,
                    "ttl_seconds_remaining": hit.ttl_seconds_remaining,
                }
            )
            return
        dedup_recorded = True

    def _rollback_dedup() -> None:
        """Remove the dedup entry we recorded above, if any.

        Called on every failure path between the record call and the
        successful completion of playback. Without this, a failure
        would leave a zombie entry in ``ctx.once_dedup._seen`` that
        would incorrectly dedup the next retry of the same text.
        """
        if dedup_recorded:
            ctx.once_dedup.rollback(text)

    logger.info(
        "Synthesize: id=%s provider=%s voice=%s chars=%d",
        request_id,
        provider_name,
        resolved_voice,
        len(text),
    )

    # Local providers (espeak, say) play directly to the audio device,
    # bypassing the synthesize-cache-enqueue pipeline. Cloud providers
    # are skipped entirely so we don't pay for provider construction.
    if provider_name in _LOCAL_PROVIDERS:
        direct_result = await _try_direct_play(
            text=text,
            voice=voice,
            provider_name=provider_name,
            model=model,
            language=language,
            rate=rate,
            vibe_tags=vibe_tags,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=speaker_boost,
            api_key=api_key,
            ctx=ctx,
        )
        if isinstance(direct_result, Exception):
            _rollback_dedup()
            await websocket.send_json(
                {
                    "type": "error",
                    "id": request_id,
                    "message": str(direct_result),
                }
            )
            return
        if direct_result is not None:
            if direct_result == 0:
                await websocket.send_json({"type": "done", "id": request_id})
            else:
                _rollback_dedup()
                await websocket.send_json(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": f"play_directly failed with rc={direct_result}",
                    }
                )
            return

    try:
        output_path = await _synthesize_to_file(
            text,
            voice,
            provider_name,
            model,
            language,
            rate,
            vibe_tags,
            stability,
            similarity,
            style,
            speaker_boost,
            api_key,
            request_id=request_id,
        )
    except Exception as exc:
        _rollback_dedup()
        logger.exception("Synthesis failed for id=%s", request_id)
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": str(exc)}
        )
        return

    # Enqueue for playback
    done_event = asyncio.Event()
    await ctx.playback_queue.put(
        PlaybackItem(path=output_path, request_id=request_id, notify=done_event)
    )
    await websocket.send_json({"type": "playing", "id": request_id})
    await done_event.wait()
    # Client may have already closed the connection after receiving 'playing'.
    # Suppress any send failure — the audio has already played.
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await websocket.send_json({"type": "done", "id": request_id})


async def _handle_record(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'record' message: TTS without playback, return audio bytes."""
    request_id = str(msg.get("id", ""))
    text = str(msg.get("text", ""))
    if not text:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "empty text"}
        )
        return

    voice = _parse_optional_str(msg, "voice")
    provider_name = _parse_optional_str(msg, "provider") or auto_detect_provider()
    model = _parse_optional_str(msg, "model")
    rate = _parse_optional_int(msg, "rate")
    language = _parse_optional_str(msg, "language")
    vibe_tags = _parse_optional_str(msg, "vibe_tags")
    stability = _parse_optional_float(msg, "stability")
    similarity = _parse_optional_float(msg, "similarity")
    style = _parse_optional_float(msg, "style")
    speaker_boost_raw = msg.get("speaker_boost")
    speaker_boost = bool(speaker_boost_raw) if speaker_boost_raw is not None else None
    api_key = _parse_optional_str(msg, "api_key")

    logger.info(
        "Record: id=%s provider=%s voice=%s chars=%d",
        request_id,
        provider_name,
        voice or "",
        len(text),
    )

    try:
        output_path = await _synthesize_to_file(
            text,
            voice,
            provider_name,
            model,
            language,
            rate,
            vibe_tags,
            stability,
            similarity,
            style,
            speaker_boost,
            api_key,
            request_id=request_id,
        )
    except Exception as exc:
        logger.exception("Record synthesis failed for id=%s", request_id)
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": str(exc)}
        )
        return

    audio_data = output_path.read_bytes()
    # Only unlink tempfiles produced by a fresh synthesis. Cache-hit
    # paths return the on-disk entry directly, and removing it would
    # poison every subsequent identical request — including the
    # anti-poison invariant TestCacheApiKeyBypass exercises. The
    # CACHE_DIR lookup goes through the module (``_cache_module``)
    # instead of a bound import so tests that monkey-patch
    # ``punt_vox.cache.CACHE_DIR`` to a tmp dir stay in sync with the
    # handler's view of what counts as a cache-owned path.
    is_cache_owned = output_path.is_relative_to(_cache_module.CACHE_DIR)
    if not is_cache_owned:
        output_path.unlink(missing_ok=True)  # clean up temp file
    encoded = base64.b64encode(audio_data).decode("ascii")
    await websocket.send_json({"type": "audio", "id": request_id, "data": encoded})


async def _handle_chime(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'chime' message: play a bundled chime sound."""
    signal = str(msg.get("signal", "done"))
    path = _resolve_chime(signal)
    if path is None:
        logger.warning("Unknown chime signal: %s", signal)
        await websocket.send_json(
            {"type": "error", "id": "", "message": f"unknown chime: {signal}"}
        )
        return

    # Chimes are always deduped with a fixed window — user explicitly
    # confirmed this behavior is desired in vox-0e9 scoping. Unlike
    # speech, chimes do not opt in via a `once` flag.
    if not ctx.chime_dedup.should_play(signal):
        logger.info("Dedup: skipping duplicate chime %s", signal)
        await websocket.send_json({"type": "done", "id": ""})
        return

    logger.info("Chime: %s", signal)
    done_event = asyncio.Event()
    await ctx.playback_queue.put(
        PlaybackItem(path=path, request_id=f"chime:{signal}", notify=done_event)
    )
    await websocket.send_json({"type": "playing", "id": f"chime:{signal}"})
    await done_event.wait()
    # Client may have already closed the connection after receiving 'playing'.
    # Suppress any send failure — the audio has already played.
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await websocket.send_json({"type": "done", "id": f"chime:{signal}"})


async def _handle_voices(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'voices' message: list available voices."""
    provider_name = _parse_optional_str(msg, "provider") or auto_detect_provider()

    try:
        provider = get_provider(provider_name, config_path=None)
        voice_list = await asyncio.to_thread(provider.list_voices)
    except Exception as exc:
        logger.exception("Voice listing failed for provider=%s", provider_name)
        await websocket.send_json(
            {
                "type": "error",
                "id": "",
                "message": f"voice listing failed: {exc}",
            }
        )
        return

    await websocket.send_json(
        {"type": "voices", "provider": provider_name, "voices": voice_list}
    )


def _health_payload_minimal(ctx: DaemonContext) -> dict[str, object]:
    """Return the public health payload safe for unauthenticated callers.

    Excludes ``audio_env``, ``player_binary``, and ``last_playback`` so the
    HTTP ``/health`` route can never leak environment variables or stderr
    contents to non-localhost listeners.
    """
    from punt_vox.providers import auto_detect_provider

    uptime = time.monotonic() - ctx.start_time
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "queued": ctx.playback_queue.qsize(),
        "port": ctx.port,
        "active_sessions": ctx.client_count,
        "provider": auto_detect_provider(),
    }


def _health_payload_full(ctx: DaemonContext) -> dict[str, object]:
    """Return the full diagnostic health payload for authenticated callers.

    Adds the audio environment snapshot, the resolved player binary, the
    last playback result, the running process id, and the cached daemon
    version. Used only by the WebSocket health handler, which is gated
    by the auth token.

    The ``pid`` field is used by ``vox daemon restart`` to confirm the
    daemon has come back up as a fresh process. The ``daemon_version``
    field is used by ``vox doctor`` to warn when the running daemon
    does not match the wheel installed on disk (vox-nmb). Neither is
    exposed on the unauthenticated HTTP ``/health`` route — version
    info is a fingerprinting aid for targeted exploitation, and the
    minimal payload stays minimal.
    """
    payload = _health_payload_minimal(ctx)
    payload["audio_env"] = {k: os.environ.get(k, "<unset>") for k in _AUDIO_ENV_KEYS}
    payload["player_binary"] = _player_binary_path()
    payload["last_playback"] = ctx.last_playback
    payload["pid"] = os.getpid()
    payload["daemon_version"] = ctx.daemon_version
    return payload


async def _handle_health(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'health' message over the authenticated WebSocket."""
    payload = _health_payload_full(ctx)
    payload["type"] = "health"
    await websocket.send_json(payload)


# ---------------------------------------------------------------------------
# Music loop and handlers
# ---------------------------------------------------------------------------

_MUSIC_DURATION_MS = 120_000
_MUSIC_MAX_RETRIES = 3


async def _music_backoff_sleep(seconds: float) -> None:
    """Sleep for backoff in the music loop. Patchable by tests."""
    await asyncio.sleep(seconds)


def _slugify(text: str, max_len: int = 40) -> str:
    """Slugify a string for use in filenames."""
    import re

    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return slug[:max_len]


async def _kill_music_proc(ctx: DaemonContext) -> None:
    """Kill the current music subprocess if running."""
    proc = ctx.music_proc
    if proc is not None and proc.returncode is None:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
    ctx.music_proc = None


def _auto_track_name(ctx: DaemonContext) -> str:
    """Derive a short auto-name from vibe + style + HHMM."""
    vibe, _ = ctx.music_vibe
    style = ctx.music_style
    hhmm = time.strftime("%H%M")
    vibe_part = _slugify(vibe, max_len=20) if vibe else "ambient"
    style_part = _slugify(style, max_len=20) if style else "mix"
    return f"{vibe_part}-{style_part}-{hhmm}"


async def _generate_music_track(ctx: DaemonContext) -> Path:
    """Generate a music track from the current vibe and style.

    Extracted so the generation phase can run as an ``asyncio.Task``
    concurrently with the playback loop.

    When ``ctx.music_track_name`` is set, the track is saved under
    that name in ``~/vox-output/music/``. Otherwise an auto-derived
    name from vibe + style + HHMM is used. The name is stored back
    into ``ctx.music_track_name`` for status reporting.
    """
    vibe, vibe_tags = ctx.music_vibe
    style = ctx.music_style

    from punt_vox.music import vibe_to_prompt

    hour = time.localtime().tm_hour
    # TODO(vox-0qi): pass session signals for work-intensity layer
    prompt = vibe_to_prompt(
        vibe or None, vibe_tags or None, style or None, hour, signals=[]
    )

    output_dir = _music_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use explicit name if set, otherwise auto-derive.
    track_name = ctx.music_track_name or _auto_track_name(ctx)
    safe_name = _slugify(track_name, max_len=60)
    filename = f"{safe_name}.mp3"
    output_path = output_dir / filename

    # Store the resolved name back for status reporting.
    ctx.music_track_name = safe_name

    from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider

    provider = ElevenLabsMusicProvider()
    return await provider.generate_track(prompt, _MUSIC_DURATION_MS, output_path)


async def _music_loop(ctx: DaemonContext) -> None:
    """Background task: generate and loop music tracks.

    Runs for the lifetime of the daemon.  When ``music_mode`` is "on",
    derives a prompt from the current vibe, generates a track via
    :class:`~punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider`,
    and loops it via ffplay at reduced volume.

    The key invariant is **gapless handoff**: the old track keeps
    looping in its own playback subprocess while generation runs as a
    concurrent ``asyncio.Task``.  The old subprocess is killed only
    once the new track is ready.  If the vibe changes again during
    generation, the in-flight generation task is cancelled and a fresh
    one starts — the old track keeps looping throughout.

    Crash recovery: on failure, logs the error, resets state, and
    retries with backoff (3 attempts).  After 3 failures, sets
    ``music_mode`` to "off".
    """
    retry_count = 0
    current_track: Path | None = None
    gen_task: asyncio.Task[Path] | None = None

    while True:
        # Wait until music is turned on.
        while ctx.music_mode != "on":
            ctx.music_changed.clear()
            # Re-check after clear to avoid lost wakeup: a handler may
            # have set music_mode between our check and the clear().
            if ctx.music_mode == "on":
                break
            await ctx.music_changed.wait()

        try:
            # --- Initial generation (no old track to loop) ----------------
            if current_track is None:
                ctx.music_state = "generating"
                ctx.music_changed.clear()
                current_track = await _generate_music_track(ctx)
                ctx.music_track = current_track
                retry_count = 0

                # Vibe changed during initial generation — regenerate
                # immediately (no old track to keep looping).
                if ctx.music_changed.is_set():
                    logger.info("Vibe changed during initial generation, regenerating")
                    current_track = None
                    continue

            # --- Playback loop: loop current_track, generate in parallel --
            assert current_track is not None  # guaranteed by initial generation above
            gen_task = None
            while ctx.music_mode == "on":
                ctx.music_state = "playing" if gen_task is None else "generating"
                cmd = _music_player_command(current_track)
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                ctx.music_proc = proc

                # Wait on this subprocess, re-entering the wait when
                # only a vibe change fired (the proc is still alive).
                proc_done = False
                handoff_occurred = False
                while not proc_done:
                    wait_task = asyncio.create_task(proc.wait())
                    changed_task = asyncio.create_task(
                        ctx.music_changed.wait(),
                    )
                    waitables: set[asyncio.Future[object]] = {
                        cast("asyncio.Future[object]", wait_task),
                        cast("asyncio.Future[object]", changed_task),
                    }
                    if gen_task is not None:
                        waitables.add(cast("asyncio.Future[object]", gen_task))

                    _done, pending = await asyncio.wait(
                        waitables,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        # Don't cancel the generation task — it may
                        # still be running and we want it to finish.
                        if t is gen_task:
                            continue
                        t.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await t

                    # --- /music off: kill everything immediately ----------
                    if ctx.music_mode != "on":
                        if gen_task is not None and not gen_task.done():
                            gen_task.cancel()
                            with contextlib.suppress(
                                asyncio.CancelledError,
                            ):
                                await gen_task
                            gen_task = None
                        await _kill_music_proc(ctx)
                        current_track = None
                        proc_done = True
                        break

                    # --- Generation task completed: handoff ---------------
                    if gen_task is not None and gen_task.done():
                        exc = gen_task.exception()
                        if exc is not None:
                            gen_task = None
                            raise exc
                        new_track = gen_task.result()
                        gen_task = None
                        ctx.music_track = new_track
                        retry_count = 0
                        await _kill_music_proc(ctx)
                        current_track = new_track
                        # Don't clear music_changed here — a vibe change
                        # may have arrived during generation.  The next
                        # iteration's is_set() check will catch it.
                        handoff_occurred = True
                        proc_done = True
                        break

                    # --- Vibe changed: start/restart generation -----------
                    if ctx.music_changed.is_set():
                        ctx.music_changed.clear()
                        if gen_task is not None and not gen_task.done():
                            gen_task.cancel()
                            with contextlib.suppress(
                                asyncio.CancelledError,
                            ):
                                await gen_task
                        ctx.music_state = "generating"
                        gen_task = asyncio.create_task(
                            _generate_music_track(ctx),
                        )
                        # Don't kill the proc — let it keep looping.
                        # Re-enter this inner wait loop so we race
                        # the same proc against the new gen_task.
                        continue

                    # --- Subprocess ended naturally -----------------------
                    proc_done = True

                # If music was turned off, the outer while condition
                # handles exiting.  Otherwise fall through to respawn
                # the subprocess (same or new track).
                if current_track is None:
                    break

                # After handoff, the old proc was killed intentionally —
                # non-zero rc is expected, not worth warning about.
                if not handoff_occurred:
                    rc = proc.returncode
                    if rc is not None and rc != 0:
                        logger.warning(
                            "Music playback ended with rc=%s for %s",
                            rc,
                            current_track.name,
                        )

        except asyncio.CancelledError:
            if gen_task is not None and not gen_task.done():
                gen_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await gen_task
                gen_task = None
            await _kill_music_proc(ctx)
            ctx.music_state = "idle"
            current_track = None
            raise
        except Exception:
            logger.exception(
                "Music loop error (attempt %d/%d)",
                retry_count + 1,
                _MUSIC_MAX_RETRIES,
            )
            if gen_task is not None and not gen_task.done():
                gen_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await gen_task
                gen_task = None
            await _kill_music_proc(ctx)
            ctx.music_state = "idle"
            current_track = None
            retry_count += 1
            if retry_count >= _MUSIC_MAX_RETRIES:
                logger.error(
                    "Music loop failed %d times, disabling music", _MUSIC_MAX_RETRIES
                )
                ctx.music_mode = "off"
                retry_count = 0
            else:
                # Exponential backoff: 1s, 2s, 4s...
                await _music_backoff_sleep(2 ** (retry_count - 1))


async def _handle_music_on(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_on' message: start or transfer music ownership.

    Ownership transfer is atomic: kill existing subprocess, update all
    state fields, then signal MusicLoop. No interleaving.

    When a ``name`` field is present and a track with that name already
    exists on disk, the existing file is replayed without generation
    (zero credits).
    """
    request_id = str(msg.get("id", ""))
    owner_id = str(msg.get("owner_id", ""))
    style = str(msg.get("style", ""))
    vibe = str(msg.get("vibe", ""))
    vibe_tags = str(msg.get("vibe_tags", ""))
    name = str(msg.get("name", ""))

    if not owner_id:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "owner_id is required"}
        )
        return

    # Check for existing track by name -- skip generation if found.
    if name:
        safe_name = _slugify(name, max_len=60)
        existing_path = _music_output_dir() / f"{safe_name}.mp3"
        if existing_path.exists():
            await _kill_music_proc(ctx)
            ctx.music_mode = "on"
            if style:
                ctx.music_style = style
            ctx.music_owner = owner_id
            ctx.music_vibe = (vibe, vibe_tags)
            ctx.music_track = existing_path
            ctx.music_track_name = safe_name
            ctx.music_state = "playing"
            ctx.music_changed.set()

            logger.info(
                "Music on (replay): owner=%s name=%s track=%s",
                owner_id,
                safe_name,
                existing_path,
            )
            await websocket.send_json(
                {
                    "type": "music_on",
                    "id": request_id,
                    "status": "playing",
                    "track": str(existing_path),
                    "name": safe_name,
                }
            )
            return

    # Atomic ownership transfer: kill existing, update all fields, signal.
    await _kill_music_proc(ctx)

    ctx.music_mode = "on"
    if style:
        ctx.music_style = style
    ctx.music_owner = owner_id
    ctx.music_vibe = (vibe, vibe_tags)
    ctx.music_track_name = _slugify(name, max_len=60) if name else ""
    ctx.music_state = "generating"
    ctx.music_changed.set()

    logger.info(
        "Music on: owner=%s style=%s vibe=%s name=%s",
        owner_id,
        ctx.music_style,
        vibe,
        ctx.music_track_name,
    )
    await websocket.send_json(
        {"type": "music_on", "id": request_id, "status": "generating"}
    )


async def _handle_music_off(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_off' message: stop music playback."""
    request_id = str(msg.get("id", ""))

    await _kill_music_proc(ctx)
    ctx.music_mode = "off"
    ctx.music_state = "idle"
    ctx.music_changed.set()

    logger.info("Music off")
    await websocket.send_json(
        {"type": "music_off", "id": request_id, "status": "stopped"}
    )


async def _handle_music_play(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_play' message: replay a saved track by name."""
    request_id = str(msg.get("id", ""))
    name = str(msg.get("name", ""))
    owner_id = str(msg.get("owner_id", ""))

    if not name:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "name is required"}
        )
        return

    if not owner_id:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "owner_id is required"}
        )
        return

    safe_name = _slugify(name, max_len=60)
    track_path = _music_output_dir() / f"{safe_name}.mp3"

    if not track_path.exists():
        await websocket.send_json(
            {
                "type": "error",
                "id": request_id,
                "message": f"track not found: {safe_name}",
            }
        )
        return

    # Kill current playback, set up replay.
    await _kill_music_proc(ctx)
    ctx.music_mode = "on"
    ctx.music_owner = owner_id
    ctx.music_track = track_path
    ctx.music_track_name = safe_name
    ctx.music_state = "playing"
    ctx.music_changed.set()

    logger.info(
        "Music play: owner=%s name=%s track=%s",
        owner_id,
        safe_name,
        track_path,
    )
    await websocket.send_json(
        {
            "type": "music_play",
            "id": request_id,
            "status": "playing",
            "track": str(track_path),
            "name": safe_name,
        }
    )


async def _handle_music_list(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_list' message: return saved tracks with metadata."""
    request_id = str(msg.get("id", ""))

    output_dir = _music_output_dir()
    tracks: list[dict[str, object]] = []

    if output_dir.exists():
        for mp3 in sorted(output_dir.glob("*.mp3")):
            stat = mp3.stat()
            tracks.append(
                {
                    "name": mp3.stem,
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                    "path": str(mp3),
                }
            )

    await websocket.send_json(
        {
            "type": "music_list",
            "id": request_id,
            "tracks": tracks,
        }
    )


async def _handle_music_vibe(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_vibe' message: update vibe if sender is owner."""
    request_id = str(msg.get("id", ""))
    owner_id = str(msg.get("owner_id", ""))
    vibe = str(msg.get("vibe", ""))
    vibe_tags = str(msg.get("vibe_tags", ""))

    if not owner_id:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "owner_id is required"}
        )
        return

    if owner_id != ctx.music_owner:
        await websocket.send_json(
            {"type": "music_vibe", "id": request_id, "status": "ignored"}
        )
        return

    new_vibe = (vibe, vibe_tags)
    if new_vibe == ctx.music_vibe:
        await websocket.send_json(
            {"type": "music_vibe", "id": request_id, "status": "ignored"}
        )
        return

    ctx.music_vibe = new_vibe
    ctx.music_changed.set()

    logger.info("Music vibe changed: vibe=%s tags=%s", vibe, vibe_tags)
    await websocket.send_json(
        {"type": "music_vibe", "id": request_id, "status": "generating"}
    )


# ---------------------------------------------------------------------------
# WebSocket route
# ---------------------------------------------------------------------------


_HANDLERS: dict[
    str,
    Callable[
        [dict[str, object], WebSocket, DaemonContext],
        object,
    ],
] = {
    "synthesize": _handle_synthesize,
    "chime": _handle_chime,
    "record": _handle_record,
    "voices": _handle_voices,
    "health": _handle_health,
    "music_on": _handle_music_on,
    "music_off": _handle_music_off,
    "music_play": _handle_music_play,
    "music_list": _handle_music_list,
    "music_vibe": _handle_music_vibe,
}


async def _ws_route(websocket: WebSocket) -> None:
    """Main WebSocket route at /ws."""
    ctx: DaemonContext = websocket.app.state.ctx

    if not _check_auth(websocket, ctx):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    ctx.client_count += 1
    logger.info("Client connected (total: %d)", ctx.client_count)

    try:
        while True:
            # Preempt Starlette's RuntimeError on a peer-closed socket.
            # After the vox-ehf fix in 4.3.0, chime/unmute clients return
            # on the ``"playing"`` ack and close the WebSocket while this
            # loop is still awaiting the next ``receive_text()``. The
            # handler's trailing ``contextlib.suppress(WebSocketDisconnect,
            # RuntimeError)`` send of the stale ``"done"`` message lands
            # on the peer-closed socket and Starlette transitions the
            # ``application_state`` to ``DISCONNECTED`` synchronously. If
            # we did not check the state here, the next ``receive_text()``
            # would raise ``RuntimeError('WebSocket is not connected. Need
            # to call "accept" first.')`` — not ``WebSocketDisconnect`` —
            # and fall through to ``except Exception`` below, logging a
            # full traceback on every chime/unmute/recap. Checking the
            # state ourselves lets us break out cleanly without widening
            # the outer exception surface (vox-ewh).
            if websocket.application_state != WebSocketState.CONNECTED:
                break
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "id": "", "message": "invalid JSON"}
                )
                continue

            if not isinstance(msg, dict):
                await websocket.send_json(
                    {"type": "error", "id": "", "message": "expected JSON object"}
                )
                continue

            msg_type = str(msg.get("type", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
            handler = _HANDLERS.get(msg_type)
            if handler is None:
                msg_id = str(msg.get("id", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                await websocket.send_json(
                    {
                        "type": "error",
                        "id": msg_id,
                        "message": f"unknown message type: {msg_type}",
                    }
                )
                continue

            # Each message handler is awaited in the receive loop.
            # Multiple clients are concurrent (each has its own receive loop),
            # but messages from a single client are processed sequentially.
            await handler(msg, websocket, ctx)  # type: ignore[misc]
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        ctx.client_count -= 1
        logger.info("Client disconnected (total: %d)", ctx.client_count)


# ---------------------------------------------------------------------------
# HTTP health route
# ---------------------------------------------------------------------------


async def _health_route(request: Request) -> JSONResponse:
    """Unauthenticated HTTP health endpoint -- minimal payload only."""
    ctx: DaemonContext = request.app.state.ctx
    return JSONResponse(_health_payload_minimal(ctx))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(
    ctx: DaemonContext | None = None,
    *,
    lifespan: (Callable[[Starlette], AbstractAsyncContextManager[None]] | None) = None,
) -> Starlette:
    """Build the Starlette ASGI application.

    Exposed as a factory so tests can construct the app without starting
    uvicorn.
    """
    if ctx is None:
        ctx = DaemonContext()

    routes: list[Route | WebSocketRoute] = [
        Route("/health", _health_route, methods=["GET"]),
        WebSocketRoute("/ws", _ws_route),
    ]

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.ctx = ctx
    return app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

cli = typer.Typer(add_completion=False)


@cli.callback(invoke_without_command=True)
def main(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Listen port"),
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Listen host"),
) -> None:
    """Start the voxd audio server daemon."""
    # Create (or tighten) per-user state dirs before anything else
    # touches the filesystem. ``ensure_user_dirs`` forces mode 0700 on
    # ``~/.punt-labs/vox`` and its ``logs``/``run``/``cache``
    # subdirectories, including pre-existing dirs that were created
    # under a looser umask in earlier versions. Every subsequent
    # ``Path.mkdir(..., exist_ok=True)`` call in voxd inherits the
    # already-tightened permissions because the directory is already
    # present with mode 0700.
    ensure_user_dirs()

    run_dir = _run_dir()
    config_dir = _config_dir()
    log_dir = _log_dir()

    # Configure logging
    _configure_logging(log_dir)
    _log_voxd_environment()

    # Load provider keys
    loaded_keys = _load_keys(config_dir)
    if loaded_keys:
        logger.info("Loaded provider keys from %s: %s", config_dir, sorted(loaded_keys))
    else:
        logger.info("No provider keys loaded from %s", config_dir)

    # Auth token
    auth_token = _read_or_create_token(run_dir)
    ctx = DaemonContext(auth_token=auth_token, port=port)

    logger.info("Starting voxd on %s:%d", host, port)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # Start playback consumer
        consumer_task = asyncio.create_task(_playback_consumer(ctx))
        logger.info("Playback consumer started")
        # Start music loop
        music_task = asyncio.create_task(_music_loop(ctx))
        logger.info("Music loop started")
        try:
            yield
        finally:
            music_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await music_task
            # Kill any lingering music subprocess.
            await _kill_music_proc(ctx)
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            _remove_port_file(run_dir)
            logger.info("voxd stopped")

    app = build_app(ctx, lifespan=lifespan)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # Write port file after bind
    original_startup = server.startup

    async def _startup_with_port_file(
        sockets: list[socket] | None = None,
    ) -> None:
        await original_startup(sockets=sockets)
        if server.servers and server.servers[0].sockets:
            actual_port = server.servers[0].sockets[0].getsockname()[1]
            _write_port_file(run_dir, actual_port)
            logger.info("voxd listening on http://%s:%d", host, actual_port)
        else:
            logger.error("Server started but no bound sockets; shutting down")
            server.should_exit = True

    server.startup = _startup_with_port_file  # type: ignore[method-assign]

    server.run()


def entrypoint() -> None:
    """Console script entry point — invokes the typer CLI."""
    cli()


if __name__ == "__main__":
    cli()
